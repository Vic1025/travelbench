"""
Unit tests for scripts/migration/london_canonicalize.py.

Builds a small in-memory stub DB so we exercise _apply_plan / _validate_plan
/ _integrity_check without needing the real London corpus or an LLM.
"""

import sys
import tempfile
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.generation.db import init_db, get_connection, new_venue_id
from scripts.migration.london_canonicalize import (
    _apply_plan, _validate_plan, _integrity_check,
    _load_tag_inventory, _load_restaurant_cuisine_state, _get_city_extension,
)
from scripts.generation.handbook import UNIVERSAL_CUISINES

PASS = 0
FAIL = 0
results = []

def check(name, condition, detail=""):
    global PASS, FAIL
    status = "✅" if condition else "❌"
    if condition:
        PASS += 1
    else:
        FAIL += 1
    results.append((status, name))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


def make_stub_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name); f.close()
    init_db(db)
    conn = get_connection(db)
    conn.execute(
        "INSERT INTO city_config (city, display_name, country, centre_lat, "
        "centre_lng, radius_km, local_cuisine_label, task_dates, tag_vocabulary) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("london", "London", "UK", 51.5, -0.1, 5.0, "british", "[]", "[]")
    )

    # 3 venues — 2 restaurants, 1 museum
    v_ids = {}
    venues = [
        ("Bistro Italiano", "restaurant", None),     # backfill via tag
        ("Mystery Diner",   "restaurant", None),     # backfill via LLM/fallback
        ("Tate Modern",     "museum",     None),     # cuisine should stay NULL
    ]
    for name, cat, cuisine in venues:
        vid = new_venue_id()
        v_ids[name] = vid
        conn.execute(
            "INSERT INTO venues "
            "(venue_id, city, name, category, district, lat, lng, "
            " avg_cost_local, price_tier, recommended_visit_minutes, "
            " booking_required, has_official_site, outdoor_sensitivity, "
            " recommended_pace, traffic_tier, total_results, yelp_popularity_score, "
            " pet_friendly, wheelchair_accessible, parking_nearby, "
            " photography_allowed, noise_level, reservation_required, "
            " outside_food_allowed, family_friendly, food_available, "
            " page_status, cuisine) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (vid, "london", name, cat, "Soho", 51.5, -0.1,
             25.0, "mid", 60, 0, 0, "indoor", "moderate", "mid", 100, 0.5,
             0, 1, 0, 1, "moderate", 0, 0, 1, int(cat in ("restaurant","cafe")),
             "verified", cuisine)
        )

    # Tags: mix of in-vocab and off-vocab
    tag_seed = [
        # Bistro Italiano: italian (cuisine tag, easy backfill)
        ("italian",                v_ids["Bistro Italiano"], 1),
        ("popular-with-locals",    v_ids["Bistro Italiano"], 1),
        ("reservation-required",   v_ids["Bistro Italiano"], 1),  # rename → booking-required
        # Mystery Diner: no cuisine-vocab tag → LLM fallback path
        ("budget-friendly",        v_ids["Mystery Diner"],   1),
        ("counter-dining",         v_ids["Mystery Diner"],   1),  # rename → counter-seating
        # Tate Modern
        ("art",                    v_ids["Tate Modern"], 1),
        ("architecture-city-skyline", v_ids["Tate Modern"], 1),   # split → architecture + views
        ("bombay-cafe",            v_ids["Tate Modern"], 1),       # drop
        ("crown-jewels",           v_ids["Tate Modern"], 1),       # drop
        ("fish-and-chips",         v_ids["Tate Modern"], 1),       # extend
    ]
    for tag, vid, yelp in tag_seed:
        conn.execute(
            "INSERT INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
            (tag, "london", vid, yelp)
        )
    conn.commit()
    return db, v_ids


# ─── [1] Read-side helpers ───────────────────────────────────────────────────
print("\n[1] Read-side helpers")
db, v_ids = make_stub_db()
conn = get_connection(db)

inv = _load_tag_inventory(conn, "london")
inv_dict = dict(inv)
check("inventory contains italian", "italian" in inv_dict)
check("inventory contains compound", "architecture-city-skyline" in inv_dict)
check("inventory total tags == 10", sum(inv_dict.values()) == 10, str(sum(inv_dict.values())))

state = _load_restaurant_cuisine_state(conn, "london", UNIVERSAL_CUISINES)
state_by_name = {s["name"]: s for s in state}
check("Bistro Italiano has italian tag", "italian" in state_by_name["Bistro Italiano"]["cuisine_tags_matched"])
check("Mystery Diner has 0 cuisine tags",
      len(state_by_name["Mystery Diner"]["cuisine_tags_matched"]) == 0)
check("Tate Modern not in restaurant state",
      "Tate Modern" not in state_by_name)

ext = _get_city_extension(conn, "london")
check("initial city extension is []", ext == [], str(ext))
conn.close()


# ─── [2] _validate_plan ──────────────────────────────────────────────────────
print("\n[2] _validate_plan")

bad_plan = {
    "city": "london",
    "tag_actions": {
        "x": {"action": "rename", "target": "not-a-real-tag"},
    },
    "city_extension": [],
    "cuisine_backfill": {},
}
errs = _validate_plan(bad_plan)
check("rename to off-vocab target rejected",
      any("not-a-real-tag" in e for e in errs), str(errs))

bad2 = {
    "city": "london",
    "tag_actions": {
        "y": {"action": "split", "targets": ["italian", "not-real"]},
    },
    "city_extension": [],
    "cuisine_backfill": {},
}
errs2 = _validate_plan(bad2)
check("split target off-vocab rejected",
      any("not-real" in e for e in errs2), str(errs2))

bad3 = {
    "city": "london",
    "tag_actions": {},
    "city_extension": [],
    "cuisine_backfill": {"abc": {"cuisine": "atlantis-cuisine"}},
}
errs3 = _validate_plan(bad3)
check("cuisine_backfill off-vocab rejected",
      any("atlantis-cuisine" in e for e in errs3), str(errs3))

good_plan = {
    "city": "london",
    "tag_actions": {
        # P6-T2-B: booking-required is no longer in the tag vocab (lives in
        # the booking_required column). Drop the tag instead of renaming.
        "reservation-required": {"action": "drop",
                                  "reason": "regulation-mirror; lives in booking_required column"},
        # P6-T2-B: budget-friendly no longer in vocab (lives in price_tier).
        "budget-friendly": {"action": "drop",
                             "reason": "regulation-mirror; lives in price_tier column"},
        "counter-dining": {"action": "rename", "target": "counter-seating"},
        "architecture-city-skyline": {"action": "split",
                                       "targets": ["architecture", "views"]},
        "bombay-cafe": {"action": "drop", "reason": "singleton"},
        "crown-jewels": {"action": "drop", "reason": "singleton"},
        "fish-and-chips": {"action": "extend",
                            "reason": "London-specific cuisine"},
    },
    "city_extension": ["fish-and-chips"],
    "cuisine_backfill": {
        v_ids["Bistro Italiano"]: {"name": "Bistro Italiano",
                                     "source": "tag", "cuisine": "italian"},
        v_ids["Mystery Diner"]:   {"name": "Mystery Diner",
                                     "source": "llm", "cuisine": "american"},
    },
}
errs_good = _validate_plan(good_plan)
check("good plan validates clean", len(errs_good) == 0, str(errs_good))


# ─── [3] _apply_plan ─────────────────────────────────────────────────────────
print("\n[3] _apply_plan")
conn = get_connection(db)
conn.execute("BEGIN")
summary = _apply_plan(conn, good_plan)
conn.commit()
conn.close()

check("renamed count = 1", summary["tags_renamed"] == 1, str(summary))
check("split count = 1", summary["tags_split"] == 1, str(summary))
check("dropped count = 4", summary["tags_dropped"] == 4, str(summary))
check("extended count = 1", summary["tags_extended"] == 1, str(summary))
check("cuisine_set_count = 2", summary["cuisine_set_count"] == 2, str(summary))

conn = get_connection(db)
post_tags = {r["tag"] for r in conn.execute(
    "SELECT DISTINCT tag FROM tags WHERE city = 'london'"
).fetchall()}
check("reservation-required gone", "reservation-required" not in post_tags)
check("booking-required NOT created (now regulation-only)",
      "booking-required" not in post_tags)
check("counter-dining gone", "counter-dining" not in post_tags)
check("counter-seating present (renamed)", "counter-seating" in post_tags)
check("architecture-city-skyline gone", "architecture-city-skyline" not in post_tags)
check("architecture present (split)", "architecture" in post_tags)
check("views present (split)", "views" in post_tags)
check("bombay-cafe dropped", "bombay-cafe" not in post_tags)
check("crown-jewels dropped", "crown-jewels" not in post_tags)
check("fish-and-chips remains (extend)", "fish-and-chips" in post_tags)

# Cuisine backfill
cuisines = dict(conn.execute(
    "SELECT name, cuisine FROM venues WHERE city = 'london'"
).fetchall())
check("Bistro Italiano cuisine = italian", cuisines["Bistro Italiano"] == "italian")
check("Mystery Diner cuisine = american", cuisines["Mystery Diner"] == "american")
check("Tate Modern cuisine still NULL", cuisines["Tate Modern"] is None)

# City extension
ext_after = _get_city_extension(conn, "london")
check("city_extension persisted", ext_after == ["fish-and-chips"], str(ext_after))


# ─── [4] Integrity check post-apply ──────────────────────────────────────────
print("\n[4] _integrity_check")
issues = _integrity_check(conn, "london")
check("integrity check clean after apply", len(issues) == 0, str(issues))


# ─── [5] Idempotency — applying twice produces same state ────────────────────
print("\n[5] Idempotency")
tags_before = sorted((r["tag"], r["venue_id"]) for r in conn.execute(
    "SELECT tag, venue_id FROM tags WHERE city = 'london'"
).fetchall())
conn.close()

conn = get_connection(db)
conn.execute("BEGIN")
_apply_plan(conn, good_plan)
conn.commit()
conn.close()

conn = get_connection(db)
tags_after = sorted((r["tag"], r["venue_id"]) for r in conn.execute(
    "SELECT tag, venue_id FROM tags WHERE city = 'london'"
).fetchall())
check("second apply produces identical tag state",
      tags_before == tags_after,
      f"before={len(tags_before)} after={len(tags_after)}")
conn.close()


# ─── Summary ─────────────────────────────────────────────────────────────────
db.unlink()
# Clean up any backup files
for bak in db.parent.glob(f"{db.stem}.bak.*.db"):
    bak.unlink()

print(f"\n{'='*55}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL == 0:
    print(f"✅ All Slice 3 migration tests passed ({PASS}/{PASS + FAIL})")
    sys.exit(0)
else:
    sys.exit(1)
