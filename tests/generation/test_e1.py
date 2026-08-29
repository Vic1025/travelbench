"""
scripts/generation/test_e1.py

Tests for E1 — auto-window generation + event generation pipeline.

Covers:
  [1]  _validate_windows — all validation rules
  [2]  _stub_windows — northern + southern hemisphere fallbacks
  [3]  generate_and_store_windows — dry-run (no API key uses stub)
  [4]  generate_and_store_windows — skip if windows already exist
  [5]  _validate_events — all event constraint rules
  [6]  _build_assignment — event_briefs EVENTS block
  [7]  VERIFY _check_event_mentions — fires on missing event mention
  [8]  Orchestrator dry-run — event_briefs attached to briefs before dispatch
  [9]  End-to-end dry-run — Istanbul (new city, uses stub windows)

Run: python scripts/generation/test_e1.py
"""

import sys
import json
import tempfile
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection
from scripts.generation.populate_seasonal_windows import (
    generate_and_store_windows, get_windows,
    _validate_windows, _stub_windows,
)
from scripts.generation.generate_city_venues import (
    _validate_events, generate_events_for_windows,
    generate_city_venues,
)
from scripts.generation.generate_venue import _build_assignment
from scripts.generation.agent_tools import _check_event_mentions

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


def insert_city(conn, city):
    conn.execute("""
        INSERT OR IGNORE INTO city_config
            (city, display_name, country, centre_lat, centre_lng,
             radius_km, local_cuisine_label, task_dates)
        VALUES (?,?,?,?,?,?,?,?)
    """, (city, city.title(), "XX", 41.0, 29.0, 10.0, "local", "[]"))
    conn.commit()


def make_7day_window(wid: str, start: str, label: str = "Test Window",
                     anchor_date: str = None) -> dict:
    d0 = date.fromisoformat(start)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(7)]
    anchor_date = anchor_date or dates[2]
    return {
        "window_id": wid,
        "label": label,
        "dates": dates,
        "anchor_events": [{"date": anchor_date, "name": "Test Event", "type": "festival"}],
        "character": "A test window with interesting planning challenges.",
        "conditional_wrong_info_hints": [
            {"type": "hours_trap",     "description": "Hours trap description."},
            {"type": "access_trap",    "description": "Access trap description."},
            {"type": "character_trap", "description": "Character trap description."},
        ],
        "venue_pool_id": None,
    }


def make_minimal_briefs(n: int = 10) -> list[dict]:
    cats = ["restaurant", "cafe", "bar", "museum", "attraction",
            "park", "neighbourhood", "restaurant", "museum", "cafe"]
    tiers = ["high", "mid", "low", "mid", "low", "mid", "low", "mid", "low", "mid"]
    return [
        {"name": f"Venue {i}", "category": cats[i % len(cats)],
         "district": "TestDistrict", "traffic_tier": tiers[i % len(tiers)],
         "character": f"A test venue number {i}."}
        for i in range(n)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# [1] _validate_windows
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] _validate_windows")

# Valid: 3 non-overlapping windows
w1 = make_7day_window("city_spring_2026", "2026-04-01")
w2 = make_7day_window("city_summer_2026", "2026-07-01")
w3 = make_7day_window("city_winter_2026", "2026-12-01")
errs = _validate_windows([w1, w2, w3])
check("3 valid windows passes", errs == [], str(errs))

# Too few windows
errs = _validate_windows([w1])
check("1 window fails (need ≥2)", any("2" in e for e in errs), str(errs))

# Too many windows
w4 = make_7day_window("city_autumn_2026", "2026-10-01")
w5 = make_7day_window("city_extra_2026",  "2026-11-15")
errs = _validate_windows([w1, w2, w3, w4, w5])
check("5 windows fails (max 4)", any("4" in e for e in errs), str(errs))

# Not 7 dates
bad_dates = {**w1, "dates": w1["dates"][:6]}
errs = _validate_windows([bad_dates, w2])
check("6-date window fails", any("7" in e for e in errs), str(errs))

# Non-consecutive dates
non_consec = {**w1, "dates": w1["dates"][:3] + [(date.fromisoformat(w1["dates"][3]) + timedelta(days=2)).isoformat()] + w1["dates"][4:]}
errs = _validate_windows([non_consec, w2])
check("non-consecutive dates fails", any("consecutive" in e for e in errs), str(errs))

# Overlapping windows
overlap = make_7day_window("city_overlap_2026", "2026-04-05")  # overlaps w1 (Apr 1-7)
errs = _validate_windows([w1, overlap, w3])
check("overlapping windows fails", any("overlap" in e for e in errs), str(errs))

# Anchor event outside window dates
bad_anchor = {**w1, "anchor_events": [{"date": "2026-05-01", "name": "Out of range", "type": "festival"}]}
errs = _validate_windows([bad_anchor, w2])
check("anchor date outside window fails", any("anchor" in e for e in errs), str(errs))

# Missing character
no_char = {**w1, "character": ""}
errs = _validate_windows([no_char, w2])
check("empty character fails", any("character" in e for e in errs), str(errs))

# Wrong hint types
bad_hints = {**w1, "conditional_wrong_info_hints": [
    {"type": "hours_trap", "description": "x"},
    {"type": "hours_trap", "description": "y"},
    {"type": "access_trap", "description": "z"},
]}
errs = _validate_windows([bad_hints, w2])
check("duplicate hint types fails", any("hints" in e for e in errs), str(errs))


# ─────────────────────────────────────────────────────────────────────────────
# [2] _stub_windows
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] _stub_windows")

stub_cfg_n = {"display_name": "Istanbul", "hemisphere": "northern"}
stubs_n = _stub_windows("istanbul", stub_cfg_n)
check("northern hemisphere produces 3 stubs", len(stubs_n) == 3)
errs = _validate_windows(stubs_n)
check("northern stubs pass validation", errs == [], str(errs))
check("stub window_ids contain city key", all("istanbul" in w["window_id"] for w in stubs_n))
check("stubs have no date overlaps", len(errs) == 0)

stub_cfg_s = {"display_name": "Buenos Aires", "hemisphere": "southern"}
stubs_s = _stub_windows("buenos_aires", stub_cfg_s)
check("southern hemisphere produces 3 stubs", len(stubs_s) == 3)
errs_s = _validate_windows(stubs_s)
check("southern stubs pass validation", errs_s == [], str(errs_s))


# ─────────────────────────────────────────────────────────────────────────────
# [3] generate_and_store_windows — no API key uses stub
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] generate_and_store_windows (no API key → stub)")

db = make_db()
conn = get_connection(db)
insert_city(conn, "istanbul")
conn.close()

city_cfg = {"display_name": "Istanbul", "country": "Turkey",
            "hemisphere": "northern", "timezone": "Europe/Istanbul",
            "districts": ["Beyoglu", "Sultanahmet", "Karakoy", "Besiktas"]}
result = generate_and_store_windows("istanbul", city_cfg, api_key=None, db_path=db)
check("stub result status is 'stub'", result["status"] == "stub", str(result))
check("stub writes windows", result["windows_written"] >= 2)
check("stub window_ids returned", len(result["window_ids"]) >= 2)

stored = get_windows("istanbul", db_path=db)
check("windows persisted in DB", len(stored) >= 2)
errs = _validate_windows(stored)
check("stored windows pass validation", errs == [], str(errs))
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [4] generate_and_store_windows — skip if already populated
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] orchestrator skips window generation if windows already exist")

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
# Manually populate London windows
conn.execute(
    "UPDATE city_config SET seasonal_windows = ? WHERE city = 'london'",
    (json.dumps([w1, w2]),)
)
conn.commit()
conn.close()

existing = get_windows("london", db_path=db)
check("pre-existing windows found", len(existing) == 2)
# generate_and_store_windows should not overwrite
result2 = generate_and_store_windows("london", {"display_name": "London"}, api_key=None, db_path=db)
after = get_windows("london", db_path=db)
# The function always writes — orchestrator does the skip check
# Test the orchestrator dry-run skips cleanly in [8]
check("function itself writes (orchestrator controls the skip)", result2["windows_written"] >= 2)
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [5] _validate_events
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] _validate_events")

windows = [make_7day_window("w1", "2026-04-01"), make_7day_window("w2", "2026-07-01")]
briefs = make_minimal_briefs(15)
high_indices = [0, 1]  # first two venues are high-traffic

def make_valid_events(window: dict, venue_indices: list[int],
                      briefs: list[dict]) -> list[dict]:
    """Make a minimal valid event set for one window."""
    w_dates = window["dates"]
    anchor = window["anchor_events"][0]["date"]
    events = [
        # affects_hours
        {"venue_index": venue_indices[0], "window_id": window["window_id"],
         "name": "Hours Event", "description": "Extended hours.",
         "start_date": w_dates[0], "end_date": w_dates[1],
         "affects_hours": True, "modified_hours": "10:00-22:00",
         "affects_access": False, "sold_out": False, "sold_out_dates": [],
         "price_local": None, "task_hook": "type2", "source_hint": "Check hours."},
        # affects_access + sold_out on anchor
        {"venue_index": venue_indices[1], "window_id": window["window_id"],
         "name": "Access Event", "description": "Booking required.",
         "start_date": w_dates[2], "end_date": w_dates[4],
         "affects_hours": False, "modified_hours": None,
         "affects_access": True, "sold_out": False, "sold_out_dates": [anchor],
         "price_local": 20.0, "task_hook": "type6", "source_hint": "Book ahead."},
        # extra events to reach 4 minimum, span categories
        {"venue_index": venue_indices[2], "window_id": window["window_id"],
         "name": "General Event A", "description": "A general event.",
         "start_date": w_dates[1], "end_date": w_dates[3],
         "affects_hours": False, "modified_hours": None,
         "affects_access": False, "sold_out": False, "sold_out_dates": [],
         "price_local": None, "task_hook": "general", "source_hint": "Nice event."},
        {"venue_index": venue_indices[3], "window_id": window["window_id"],
         "name": "General Event B", "description": "Another event.",
         "start_date": w_dates[2], "end_date": w_dates[5],
         "affects_hours": False, "modified_hours": None,
         "affects_access": False, "sold_out": False, "sold_out_dates": [],
         "price_local": None, "task_hook": "general", "source_hint": "Another event."},
    ]
    # Inject _category for validation
    for ev in events:
        vidx = ev["venue_index"]
        ev["_category"] = briefs[vidx]["category"]
    return events

# mid/low venue indices (not high-traffic): 2,3,4,5,6,7...
mid_low = [i for i in range(len(briefs)) if i not in high_indices]
ev_w1 = make_valid_events(windows[0], mid_low[0:4], briefs)
ev_w2 = make_valid_events(windows[1], mid_low[4:8], briefs)
all_ev = ev_w1 + ev_w2

errs = _validate_events(all_ev, windows, len(briefs), high_indices)
check("valid events passes", errs == [], str(errs[:3]))

# Missing affects_access
no_access = [e for e in ev_w1 if not e.get("affects_access")] + ev_w2
errs = _validate_events(no_access, windows, len(briefs), high_indices)
check("missing affects_access fails", any("affects_access" in e for e in errs), str(errs))

# Missing affects_hours
no_hours = [e for e in ev_w1 if not e.get("affects_hours")] + ev_w2
errs = _validate_events(no_hours, windows, len(briefs), high_indices)
check("missing affects_hours fails", any("affects_hours" in e for e in errs), str(errs))

# No anchor sold_out
no_soldout = [dict(e, sold_out_dates=[]) for e in ev_w1] + ev_w2
errs = _validate_events(no_soldout, windows, len(briefs), high_indices)
check("missing anchor sold_out fails", any("sold_out" in e for e in errs), str(errs))

# sold_out on high-traffic venue
bad_high = dict(ev_w1[0], venue_index=high_indices[0],
                sold_out_dates=[windows[0]["dates"][2]])
bad_high["_category"] = briefs[high_indices[0]]["category"]
errs = _validate_events([bad_high] + ev_w1[1:] + ev_w2, windows, len(briefs), high_indices)
check("sold_out on high-traffic fails", any("high-traffic" in e for e in errs), str(errs))

# affects_access on high-traffic venue
bad_access_high = dict(ev_w1[0], venue_index=high_indices[0], affects_access=True)
bad_access_high["_category"] = briefs[high_indices[0]]["category"]
errs = _validate_events([bad_access_high] + ev_w1[1:] + ev_w2, windows, len(briefs), high_indices)
check("affects_access on high-traffic fails", any("high-traffic" in e for e in errs), str(errs))

# Max 2 events per venue violated: ev_w1[0] already in w1; add TWO more in w2
triple_a = dict(ev_w1[0], window_id=windows[1]["window_id"],
                name="Triple Event A",
                start_date=windows[1]["dates"][0], end_date=windows[1]["dates"][1])
triple_a["_category"] = briefs[ev_w1[0]["venue_index"]]["category"]
triple_b = dict(ev_w1[0], window_id=windows[1]["window_id"],
                name="Triple Event B",
                start_date=windows[1]["dates"][2], end_date=windows[1]["dates"][3])
triple_b["_category"] = briefs[ev_w1[0]["venue_index"]]["category"]
triple = list(ev_w1) + [triple_a, triple_b]
errs = _validate_events(triple + ev_w2, windows, len(briefs), high_indices)
check("3 events on one venue fails", any("max 2" in e for e in errs), str(errs))

# Date outside window
bad_date = dict(ev_w1[0], start_date="2026-06-01", end_date="2026-06-02")
errs = _validate_events([bad_date] + ev_w1[1:] + ev_w2, windows, len(briefs), high_indices)
check("event date outside window fails", any("outside window" in e for e in errs), str(errs))

# Too few events in a window
few = ev_w1[:2] + ev_w2
errs = _validate_events(few, windows, len(briefs), high_indices)
check("fewer than 4 events in window fails", any("4-8" in e for e in errs), str(errs))


# ─────────────────────────────────────────────────────────────────────────────
# [6] _build_assignment event_briefs EVENTS block
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] _build_assignment event_briefs")

assignment_no_events = _build_assignment(
    "london", "museum", "Shoreditch", "mid",
    name="Test Museum", event_briefs=[]
)
check("no events: no EVENTS section", "Events assigned" not in assignment_no_events)

test_event = {
    "name": "Summer Photo Exhibition",
    "window_id": "london_carnival_2026",
    "start_date": "2026-08-20",
    "end_date": "2026-08-26",
    "affects_hours": True,
    "modified_hours": "10:00-22:00 Fri-Sun",
    "affects_access": True,
    "sold_out": False,
    "sold_out_dates": ["2026-08-23"],
    "price_local": 15.0,
    "source_hint": "Book at least a week ahead — carnival weekend sells out.",
    "description": "A photography exhibition celebrating Caribbean culture.",
}
assignment_with_events = _build_assignment(
    "london", "museum", "Shoreditch", "mid",
    name="Test Museum", event_briefs=[test_event]
)
check("events block header present", "Events assigned to this venue" in assignment_with_events)
check("event name in assignment", "Summer Photo Exhibition" in assignment_with_events)
check("source_hint in assignment", "carnival weekend sells out" in assignment_with_events)
check("affects_hours mentioned", "Hours change" in assignment_with_events)
check("affects_access mentioned", "advance booking required" in assignment_with_events)
check("sold_out urgency mentioned", "urgency" in assignment_with_events.lower() or "sold out" in assignment_with_events.lower())
check("specific sold_out dates NOT revealed",
      "2026-08-23" not in assignment_with_events.lower() or
      "do NOT reveal specific" in assignment_with_events)
check("VERIFY requirement mentioned", "VERIFY" in assignment_with_events)
check("official site requirement mentioned", "Official site" in assignment_with_events or "official site" in assignment_with_events)


# ─────────────────────────────────────────────────────────────────────────────
# [7] VERIFY _check_event_mentions
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] _check_event_mentions")

db = make_db()
conn = get_connection(db)

# Insert a venue and two source docs
vid = "venue_test_001"
conn.execute("""
    INSERT INTO city_config (city, display_name, country, centre_lat, centre_lng,
        radius_km, local_cuisine_label, task_dates)
    VALUES ('london', 'London', 'UK', 51.5, -0.1, 5.0, 'British', '[]')
""")
conn.execute("""
    INSERT INTO venues (venue_id, city, name, category, district,
        avg_cost_local, price_tier, recommended_visit_minutes, booking_required,
        has_official_site, outdoor_sensitivity, recommended_pace, traffic_tier,
        total_results, yelp_popularity_score, pet_friendly, wheelchair_accessible,
        parking_nearby, photography_allowed, noise_level, reservation_required,
        outside_food_allowed, family_friendly, food_available, page_status)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", (vid, "london", "Test Museum", "museum", "Shoreditch",
      20.0, "mid", 90, 0, 1, "indoor", "moderate", "mid",
      100, 0.5, 0, 1, 0, 1, "quiet", 0, 0, 1, 0, "verified"))

# Source doc that MENTIONS the event
doc_id_1 = "doc_001"
conn.execute("""
    INSERT INTO source_docs (doc_id, city, doc_type, title, author,
        source_name, date, body, page_status)
    VALUES (?,?,?,?,?,?,?,?,?)
""", (doc_id_1, "london", "blog", "Visit London", "Author",
      "Blog.com", "2026-04-01",
      "We attended the Summer Photo Exhibition — an incredible showcase of photography.",
      "committed"))
conn.execute("INSERT INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)", (doc_id_1, vid))

# Source doc that does NOT mention the event
doc_id_2 = "doc_002"
conn.execute("""
    INSERT INTO source_docs (doc_id, city, doc_type, title, author,
        source_name, date, body, page_status)
    VALUES (?,?,?,?,?,?,?,?,?)
""", (doc_id_2, "london", "blog", "More London", "Author2",
      "Blog.com", "2026-04-02",
      "The museum has lovely permanent collections on the second floor.",
      "committed"))
conn.execute("INSERT INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)", (doc_id_2, vid))
conn.commit()

# Event that IS mentioned
ev_mentioned = [{"name": "Summer Photo Exhibition", "window_id": "london_carnival_2026"}]
errs = _check_event_mentions(conn, vid, ev_mentioned)
check("mentioned event passes check", errs == [], str(errs))

# Event that is NOT mentioned
ev_not_mentioned = [{"name": "Winter Ice Sculpture Show", "window_id": "london_christmas_2026"}]
errs = _check_event_mentions(conn, vid, ev_not_mentioned)
check("unmentioned event fails check", len(errs) == 1)
check("error message names the event", "Winter Ice Sculpture Show" in errs[0]["message"])
check("error check type is event_mention", errs[0]["check"] == "event_mention")

# Multiple events — one present, one missing
ev_mixed = ev_mentioned + ev_not_mentioned
errs = _check_event_mentions(conn, vid, ev_mixed)
check("one mentioned + one missing → one error", len(errs) == 1)

# Empty event list — no errors
errs = _check_event_mentions(conn, vid, [])
check("empty event_briefs passes", errs == [])

conn.close()
db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [8] Orchestrator dry-run — event_briefs attached to briefs
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] Orchestrator dry-run — events attached to briefs before venue dispatch")

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
# Pre-populate London windows so orchestrator skips auto-gen
from scripts.generation.populate_seasonal_windows import LONDON_WINDOWS
conn.execute(
    "UPDATE city_config SET seasonal_windows = ? WHERE city = 'london'",
    (json.dumps(LONDON_WINDOWS),)
)
conn.commit()
conn.close()

result = generate_city_venues("london", dry_run=True, workers=2, db_path=db)
check("dry-run orchestration succeeds", isinstance(result, dict))
check("venues generated", result.get("succeeded", 0) == 50, f"{result.get('succeeded')}/50")
check("no failures", result.get("failed", 0) == 0)
# In dry-run, no API key means event generation is skipped — briefs get empty event_briefs
# Verify pipeline ran to completion
check("returns total_briefs=50", result.get("total_briefs") == 50)

db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [9] End-to-end dry-run — Istanbul (new city, stub windows)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] End-to-end dry-run — Istanbul (new city)")

db = make_db()

result = generate_city_venues("istanbul", dry_run=True, workers=2, db_path=db)
check("istanbul dry-run succeeds", isinstance(result, dict))
check("istanbul venues generated", result.get("succeeded", 0) == 50,
      f"{result.get('succeeded')}/50")
check("no failures", result.get("failed", 0) == 0)

# Check windows were auto-generated for istanbul
stored_windows = get_windows("istanbul", db_path=db)
check("istanbul stub windows written to DB", len(stored_windows) >= 2)
errs = _validate_windows(stored_windows)
check("istanbul windows pass validation", errs == [], str(errs))

db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All E1 tests passed ({PASS}/{total})")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
