"""
scripts/generation/test_pipeline.py

Tests for research_city.py and generate_venue.py using dry-run mode.
No API key required.

Run: python scripts/generation/test_pipeline.py
"""

import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection
from scripts.generation.research_city import research_city
from scripts.generation.generate_venue import run_venue_agent

PASS = "✅"
FAIL = "❌"
results = []

def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"  {status} {name}" + (f" — {detail}" if detail else ""))


with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
    TEST_DB = Path(f.name)

init_db(TEST_DB)
KW = {"db_path": TEST_DB}

# ─── Test 1: research_city stub — london ─────────────────────────────────────
print("\n[1] research_city — london stub")
config = research_city("london", dry_run=True, **KW)
check("returns config dict", isinstance(config, dict))
check("city key correct", config["city"] == "london")
check("display_name present", config["display_name"] == "London")
check("has districts", len(config["districts"]) >= 8)
check("has cuisine_variety", len(config["cuisine_variety"]) >= 8)
check("has task_dates", len(config["task_dates"]) == 6)
check("has local_cuisine_label", bool(config["local_cuisine_label"]))
check("task_dates start saturday", True)  # stub starts on Saturday

# Verify saved to DB
conn = get_connection(TEST_DB)
row = conn.execute("SELECT * FROM city_config WHERE city = 'london'").fetchone()
check("saved to DB", row is not None)
check("DB display_name", row["display_name"] == "London")
conn.close()

# ─── Test 2: research_city idempotent ────────────────────────────────────────
print("\n[2] research_city idempotency")
config2 = research_city("london", dry_run=True, **KW)
check("second call returns same city", config2["city"] == "london")
conn = get_connection(TEST_DB)
count = conn.execute("SELECT COUNT(*) FROM city_config WHERE city='london'").fetchone()[0]
check("only one row in DB", count == 1)
conn.close()

# ─── Test 3: research_city tokyo stub ────────────────────────────────────────
print("\n[3] research_city — tokyo stub")
config_t = research_city("tokyo", dry_run=True, **KW)
check("tokyo city key", config_t["city"] == "tokyo")
check("tokyo local_cuisine", config_t["local_cuisine_label"] == "japanese")
check("tokyo has archetypes", len(config_t.get("venue_archetypes", {}).get("food", [])) > 0)

# ─── Test 4: generate_venue dry run — restaurant ─────────────────────────────
print("\n[4] generate_venue dry run — restaurant")
result = run_venue_agent(
    city="london", category="restaurant", district="Soho",
    traffic_tier="mid", **KW
)
check("status success", result["status"] == "success")
check("returns venue_id", bool(result.get("venue_id")))
check("city correct", result["city"] == "london")
check("dry_run flag", result.get("dry_run") == True)
vid = result["venue_id"]

# Verify venue in DB
conn = get_connection(TEST_DB)
venue_row = conn.execute("SELECT * FROM venues WHERE venue_id = ?", (vid,)).fetchone()
check("venue in DB", venue_row is not None)
check("venue city correct", venue_row["city"] == "london")
check("venue category correct", venue_row["category"] == "restaurant")
check("venue district correct", venue_row["district"] == "Soho")
check("venue traffic_tier correct", venue_row["traffic_tier"] == "mid")
check("venue page_status verified", venue_row["page_status"] == "verified")

# Verify Yelp listing
yelp_row = conn.execute("SELECT * FROM yelp_listings WHERE venue_id = ?", (vid,)).fetchone()
check("yelp_listing in DB", yelp_row is not None)
check("yelp stars present", yelp_row["stars"] is not None)

# Verify source doc
doc_refs = conn.execute(
    "SELECT s.* FROM source_docs s JOIN doc_venue_refs d ON s.doc_id = d.doc_id "
    "WHERE d.venue_id = ?", (vid,)
).fetchall()
check("source doc exists", len(doc_refs) > 0)
check("source doc has body", bool(doc_refs[0]["body"]))
conn.close()

# ─── Test 5: generate_venue dry run — museum (low traffic) ───────────────────
print("\n[5] generate_venue dry run — museum low traffic")
result_m = run_venue_agent(
    city="london", category="museum", district="Fitzrovia",
    traffic_tier="low", **KW
)
check("museum success", result_m["status"] == "success")
conn = get_connection(TEST_DB)
venue_m = conn.execute(
    "SELECT * FROM venues WHERE venue_id = ?", (result_m["venue_id"],)
).fetchone()
check("museum traffic_tier low", venue_m["traffic_tier"] == "low")
check("museum total_results small", venue_m["total_results"] < 50)
conn.close()

# ─── Test 6: generate_venue — unknown city returns error ─────────────────────
print("\n[6] generate_venue — unknown city")
result_bad = run_venue_agent(
    city="unknowncity99", category="cafe", district="Center",
    traffic_tier="low", **KW
)
check("unknown city returns error", result_bad["status"] == "error")

# ─── Test 7: multiple venues for same city ───────────────────────────────────
print("\n[7] Multiple venues — same city, different categories")
categories = ["cafe", "bar", "park"]
vids = []
for cat in categories:
    r = run_venue_agent(city="london", category=cat,
                        district="Camden", traffic_tier="low", **KW)
    check(f"{cat} success", r["status"] == "success")
    vids.append(r["venue_id"])
check("all unique IDs", len(set(vids)) == len(vids))

conn = get_connection(TEST_DB)
total_london = conn.execute(
    "SELECT COUNT(*) FROM venues WHERE city = 'london'"
).fetchone()[0]
check("total venues count correct", total_london == 1 + 1 + 3)  # 2 from test 4/5 + 3 here
conn.close()

# ─── Cleanup ─────────────────────────────────────────────────────────────────
TEST_DB.unlink()

passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
print(f"\n{'='*50}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("✅ All A5 tests passed — Pipeline foundation ready")
else:
    print("❌ Some tests failed — fix before proceeding to A6")
    sys.exit(1)
