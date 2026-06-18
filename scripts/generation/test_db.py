"""
scripts/generation/test_db.py

Tests for db.py — verifies all tables exist with correct columns,
ID generators produce correct format, and basic insert/query works.

Run: python scripts/generation/test_db.py
"""

import sys
import sqlite3
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import (
    init_db, reset_db, get_connection,
    new_venue_id, new_doc_id, new_wrong_info_id, new_page_id,
    get_null_fields, REQUIRED_FIELDS
)

PASS = "✅"
FAIL = "❌"
results = []

def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


# ─── Use a temp DB so tests don't touch real data ────────────────────────────
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    TEST_DB = Path(f.name)

conn = init_db(TEST_DB)

# ─── Test 1: All tables exist ─────────────────────────────────────────────────
print("\n[1] Table existence")
expected_tables = [
    "city_config", "venues", "hours_overrides", "tags",
    "ticket_availability", "yelp_listings", "source_docs",
    "doc_venue_refs", "doc_venue_roles", "wrong_info",
    "official_site_docs", "draft_pages", "corruption_runs"
]
rows = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()
actual_tables = {r["name"] for r in rows}
for t in expected_tables:
    check(f"table:{t}", t in actual_tables)

# ─── Test 2: Key columns on venues table ─────────────────────────────────────
print("\n[2] Venues table columns")
cols = {r["name"] for r in conn.execute("PRAGMA table_info(venues)").fetchall()}
key_cols = [
    "venue_id", "city", "name", "category", "district", "lat", "lng",
    "hours_mon", "hours_fri", "hours_sun",
    "avg_cost_local", "lunch_cost_local", "price_tier",
    "recommended_visit_minutes", "booking_required", "has_official_site",
    "outdoor_sensitivity", "recommended_pace", "traffic_tier",
    "local_cuisine", "total_results", "yelp_popularity_score",
    "venue_difficulty_score", "recommended_time_window_end",
    "pet_friendly", "wheelchair_accessible", "noise_level",
    "dress_code", "age_restriction", "food_available", "page_status"
]
for c in key_cols:
    check(f"venues.{c}", c in cols)

# ─── Test 3: Tags table columns ───────────────────────────────────────────────
print("\n[3] Tags table")
tag_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tags)").fetchall()}
for c in ["tag", "city", "venue_id", "yelp_visible"]:
    check(f"tags.{c}", c in tag_cols)

# ─── Test 4: wrong_info table ────────────────────────────────────────────────
print("\n[4] wrong_info table")
wi_cols = {r["name"] for r in conn.execute("PRAGMA table_info(wrong_info)").fetchall()}
for c in ["wrong_info_id", "venue_id", "affected_field", "incorrect_value",
          "correct_value", "source_type", "wrong_info_category", "origin_story"]:
    check(f"wrong_info.{c}", c in wi_cols)

# ─── Test 5: ID generators ───────────────────────────────────────────────────
print("\n[5] ID generators")
vid = new_venue_id()
check("venue_id length=7", len(vid) == 7, vid)
check("venue_id alphanumeric", vid.isalnum(), vid)
check("venue_id starts with letter", vid[0].isalpha(), vid)

did = new_doc_id()
check("doc_id length=8", len(did) == 8, did)
check("doc_id alphanumeric", did.isalnum(), did)

# IDs should be unique across calls
ids = {new_venue_id() for _ in range(100)}
check("venue_id uniqueness (100 samples)", len(ids) == 100)

# ─── Test 6: Basic insert and query ──────────────────────────────────────────
print("\n[6] Basic insert and query")

# Insert city config
conn.execute("""
    INSERT INTO city_config (city, display_name, country, centre_lat, centre_lng,
        radius_km, local_cuisine_label, task_dates)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""", ("paris", "Paris", "France", 48.8566, 2.3522, 5.0, "french", '["2025-03-07"]'))
conn.commit()

row = conn.execute("SELECT * FROM city_config WHERE city = 'paris'").fetchone()
check("city_config insert+query", row is not None)
check("city_config display_name", row["display_name"] == "Paris")

# P6-T1: tag_vocabulary column exists with default '[]'
cc_cols = {r["name"] for r in conn.execute("PRAGMA table_info(city_config)").fetchall()}
check("city_config.tag_vocabulary column exists", "tag_vocabulary" in cc_cols)
check("city_config.tag_vocabulary defaults to '[]'",
      row["tag_vocabulary"] == "[]", row["tag_vocabulary"])

# P6-T1b: venues.cuisine column exists (TEXT, nullable)
v_cols = {r["name"] for r in conn.execute("PRAGMA table_info(venues)").fetchall()}
check("venues.cuisine column exists", "cuisine" in v_cols)

# P6-T2-B: source_docs.mentioned_tags column exists with default '[]'
sd_cols_info = conn.execute("PRAGMA table_info(source_docs)").fetchall()
sd_cols = {r["name"] for r in sd_cols_info}
check("source_docs.mentioned_tags column exists", "mentioned_tags" in sd_cols)
mt_default = None
for r in sd_cols_info:
    if r["name"] == "mentioned_tags":
        mt_default = r["dflt_value"]
        break
check("source_docs.mentioned_tags defaults to '[]'",
      mt_default == "'[]'", repr(mt_default))

# Insert minimal venue
vid = new_venue_id()
conn.execute("""
    INSERT INTO venues (venue_id, city, name, category, district, lat, lng,
        avg_cost_local, price_tier, recommended_visit_minutes,
        booking_required, has_official_site, outdoor_sensitivity,
        traffic_tier, total_results, yelp_popularity_score,
        pet_friendly, wheelchair_accessible, parking_nearby,
        photography_allowed, noise_level, reservation_required,
        outside_food_allowed, family_friendly, food_available)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", (vid, "paris", "Test Cafe", "cafe", "Le Marais",
      48.856, 2.352, 12.0, "budget", 45,
      0, 0, "indoor", "low", 42, 0.3,
      1, 1, 0, 1, "quiet", 0, 0, 1, 1))
conn.commit()

row = conn.execute("SELECT * FROM venues WHERE venue_id = ?", (vid,)).fetchone()
check("venue insert+query", row is not None)
check("venue name", row["name"] == "Test Cafe")
check("venue traffic_tier", row["traffic_tier"] == "low")

# Insert tag
conn.execute("INSERT INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
             ("cozy", "paris", vid, 1))
conn.execute("INSERT INTO tags (tag, city, venue_id, yelp_visible) VALUES (?,?,?,?)",
             ("hidden-gem", "paris", vid, 0))
conn.commit()

yelp_tags = conn.execute(
    "SELECT tag FROM tags WHERE venue_id = ? AND yelp_visible = 1", (vid,)
).fetchall()
all_tags = conn.execute(
    "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
).fetchall()
check("yelp_visible filter", len(yelp_tags) == 1)
check("yelp_visible tag correct", yelp_tags[0]["tag"] == "cozy")
check("all tags count", len(all_tags) == 2)

# ─── Test 7: get_null_fields ─────────────────────────────────────────────────
print("\n[7] get_null_fields helper")
nulls = get_null_fields(conn, "venue", vid)
# recommended_pace was not inserted — should be in nulls
check("null recommended_pace detected", "recommended_pace" in nulls)
# name was inserted — should NOT be in nulls
check("name not in nulls", "name" not in nulls)

# ─── Test 8: hours_overrides ─────────────────────────────────────────────────
print("\n[8] hours_overrides")
conn.execute("""
    INSERT INTO hours_overrides (venue_id, date, override_type, reason)
    VALUES (?, ?, ?, ?)
""", (vid, "2025-12-25", "closed", "Christmas Day"))
conn.commit()
override = conn.execute(
    "SELECT * FROM hours_overrides WHERE venue_id = ? AND date = ?",
    (vid, "2025-12-25")
).fetchone()
check("hours_override insert", override is not None)
check("hours_override type", override["override_type"] == "closed")
check("hours_override reason", override["reason"] == "Christmas Day")

# ─── Test 9: wrong_info table ────────────────────────────────────────────────
print("\n[9] wrong_info")
wi_id = new_wrong_info_id()
conn.execute("""
    INSERT INTO wrong_info (wrong_info_id, venue_id, affected_field,
        incorrect_value, correct_value, source_type, wrong_info_category, origin_story)
    VALUES (?,?,?,?,?,?,?,?)
""", (wi_id, vid, "hours_fri", "20:00", "22:00", "yelp", "temporal_decay",
      "Owner registered hours when cafe closed at 20:00 but extended to 22:00 in 2024 without updating Yelp."))
conn.commit()
wi = conn.execute("SELECT * FROM wrong_info WHERE wrong_info_id = ?", (wi_id,)).fetchone()
check("wrong_info insert", wi is not None)
check("wrong_info category", wi["wrong_info_category"] == "temporal_decay")
check("wrong_info origin_story", len(wi["origin_story"]) > 10)

# ─── Test 10: corruption schema (additive) ───────────────────────────────────
print("\n[10] corruption schema — fresh DB")
_CORRUPTION_WI_COLS = [
    "seed_used", "detectability", "repairability", "structure",
    "profile_id", "mask_id", "suppress_authority",
]
wi_info = conn.execute("PRAGMA table_info(wrong_info)").fetchall()
wi_cols_now = {r["name"] for r in wi_info}
for c in _CORRUPTION_WI_COLS:
    check(f"wrong_info.{c} exists (fresh)", c in wi_cols_now)
# suppress_authority defaults to 0
sa_default = next((r["dflt_value"] for r in wi_info if r["name"] == "suppress_authority"), None)
check("wrong_info.suppress_authority defaults to 0", str(sa_default) == "0", repr(sa_default))

# corruption_runs table + columns
cr_cols = {r["name"] for r in conn.execute("PRAGMA table_info(corruption_runs)").fetchall()}
for c in ["run_id", "master_seed", "profile_id", "profile_version",
          "corruptor_version", "gt_hash", "created_at"]:
    check(f"corruption_runs.{c} exists (fresh)", c in cr_cols)

# Insert exercising the new wrong_info columns
wi_id2 = new_wrong_info_id()
conn.execute("""
    INSERT INTO wrong_info (wrong_info_id, venue_id, affected_field,
        incorrect_value, correct_value, source_type, wrong_info_category, origin_story,
        seed_used, detectability, repairability, structure,
        profile_id, mask_id, suppress_authority)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
""", (wi_id2, vid, "hours_sat", "18:00", "20:00", "blog", "propagation_error",
      "Blog copied a stale aggregator listing.",
      "seed-abc", 3, 0.5, "copying_bloc", "prof-1", "mask-9", 1))
conn.commit()
wi2 = conn.execute("SELECT * FROM wrong_info WHERE wrong_info_id = ?", (wi_id2,)).fetchone()
check("wrong_info new-cols insert", wi2 is not None)
check("wrong_info.structure roundtrip", wi2["structure"] == "copying_bloc")
check("wrong_info.suppress_authority roundtrip", wi2["suppress_authority"] == 1)

# Insert into corruption_runs
conn.execute("""
    INSERT INTO corruption_runs (run_id, master_seed, profile_id, profile_version,
        corruptor_version, gt_hash, created_at)
    VALUES (?,?,?,?,?,?,?)
""", ("run-1", "master-seed", "prof-1", "v1", "c1", "deadbeef", "2026-06-18T00:00:00Z"))
conn.commit()
cr = conn.execute("SELECT * FROM corruption_runs WHERE run_id = 'run-1'").fetchone()
check("corruption_runs insert+query", cr is not None and cr["gt_hash"] == "deadbeef")

# ─── Test 11: migration on a simulated OLD db (idempotent) ───────────────────
print("\n[11] corruption migration — old DB + idempotency")
with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f2:
    OLD_DB = Path(f2.name)

# Build a pre-corruption-schema DB by hand: minimal venues + legacy wrong_info
# WITHOUT the new columns, and NO corruption_runs table.
raw = sqlite3.connect(str(OLD_DB))
raw.executescript("""
    CREATE TABLE venues (venue_id TEXT PRIMARY KEY, city TEXT);
    CREATE TABLE wrong_info (
        wrong_info_id       TEXT PRIMARY KEY,
        venue_id            TEXT NOT NULL,
        affected_field      TEXT NOT NULL,
        incorrect_value     TEXT NOT NULL,
        correct_value       TEXT NOT NULL,
        source_type         TEXT NOT NULL,
        wrong_info_category TEXT NOT NULL,
        origin_story        TEXT NOT NULL
    );
    INSERT INTO venues (venue_id, city) VALUES ('vOld', 'paris');
    INSERT INTO wrong_info (wrong_info_id, venue_id, affected_field,
        incorrect_value, correct_value, source_type, wrong_info_category, origin_story)
    VALUES ('wOld', 'vOld', 'hours_mon', '09:00', '08:00', 'yelp',
            'temporal_decay', 'legacy row predating corruption schema');
""")
raw.commit()
# Sanity: old DB lacks the new columns/table
old_wi_cols = {r[1] for r in raw.execute("PRAGMA table_info(wrong_info)").fetchall()}
old_tables = {r[0] for r in raw.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
raw.close()
check("OLD db lacks new wrong_info cols", not (set(_CORRUPTION_WI_COLS) & old_wi_cols))
check("OLD db lacks corruption_runs", "corruption_runs" not in old_tables)

# Opening via get_connection() must migrate it.
oldconn = get_connection(OLD_DB)
mig_wi = {r["name"] for r in oldconn.execute("PRAGMA table_info(wrong_info)").fetchall()}
mig_tables = {r["name"] for r in oldconn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
for c in _CORRUPTION_WI_COLS:
    check(f"migrated wrong_info.{c}", c in mig_wi)
check("migrated corruption_runs table", "corruption_runs" in mig_tables)

# Legacy row preserved and readable
legacy = oldconn.execute(
    "SELECT * FROM wrong_info WHERE wrong_info_id = 'wOld'").fetchone()
check("legacy wrong_info row preserved", legacy is not None)
check("legacy row new cols are NULL", legacy["structure"] is None and legacy["seed_used"] is None)

# A legacy-style insert (no new fields) still works post-migration
oldconn.execute("""
    INSERT INTO wrong_info (wrong_info_id, venue_id, affected_field,
        incorrect_value, correct_value, source_type, wrong_info_category, origin_story)
    VALUES (?,?,?,?,?,?,?,?)
""", ("wOld2", "vOld", "hours_tue", "10:00", "09:00", "forum",
      "conditional", "another legacy-style insert"))
oldconn.commit()
check("legacy-style insert post-migration works",
      oldconn.execute("SELECT 1 FROM wrong_info WHERE wrong_info_id='wOld2'").fetchone() is not None)

# Idempotency: capture schema, re-run migration, schema must be identical
def _schema_snapshot(c):
    return (
        tuple((r["name"], r["type"], r["dflt_value"])
              for r in c.execute("PRAGMA table_info(wrong_info)").fetchall()),
        tuple(sorted(r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())),
    )
snap_before = _schema_snapshot(oldconn)
oldconn.close()
oldconn2 = get_connection(OLD_DB)   # triggers _apply_migrations again
snap_after = _schema_snapshot(oldconn2)
check("migration is idempotent (schema unchanged on re-run)", snap_before == snap_after)
# And data still intact
check("data intact after 2nd migration",
      oldconn2.execute("SELECT COUNT(*) AS n FROM wrong_info").fetchone()["n"] == 2)
oldconn2.close()
OLD_DB.unlink()

# ─── Summary ─────────────────────────────────────────────────────────────────
conn.close()
TEST_DB.unlink()

passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("✅ All A1 tests passed — DB foundation ready")
else:
    print("❌ Some tests failed — fix before proceeding to A2")
    sys.exit(1)
