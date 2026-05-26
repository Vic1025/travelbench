"""
scripts/generation/test_b1.py

Tests for B1:
  - SQLite data loader for mock_tools (new cities)
  - get_official_site date parameter
  - Task schema extension (score_tier, hop, b_score_constraints)
  - Evaluator C/F gate behaviour
  - Paris backward compatibility
"""

import sys, json, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {label}")
        PASS += 1
    else:
        print(f"  ❌ {label}{(' — ' + str(detail)) if detail else ''}")
        FAIL += 1


# ─────────────────────────────────────────────────────────────────────────────
# [1] SQLite loader — _load_city_from_db
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] SQLite data loader")

from scripts.generation.db import init_db, get_connection, new_venue_id, new_doc_id

def make_test_db():
    """Create a minimal test DB with one london venue."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db = Path(f.name)
    f.close()
    init_db(db)
    conn = get_connection(db)

    conn.execute("""
        INSERT INTO city_config (city, display_name, country, centre_lat, centre_lng,
            radius_km, local_cuisine_label, task_dates)
        VALUES (?,?,?,?,?,?,?,?)
    """, ("london","London","UK",51.5074,-0.1278,5.0,"British","[]"))

    vid = new_venue_id()
    conn.execute("""
        INSERT INTO venues (venue_id, city, name, category, district, lat, lng,
            avg_cost_local, price_tier, recommended_visit_minutes, booking_required,
            has_official_site, outdoor_sensitivity, recommended_pace, traffic_tier,
            total_results, yelp_popularity_score, pet_friendly, wheelchair_accessible,
            photography_allowed, noise_level, family_friendly, food_available,
            page_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid,"london","Tate Modern","museum","South Bank",
          51.5076,-0.0994,0.0,"free",120,0,1,"indoor","intense","high",
          50000,0.95,0,1,1,"moderate",1,0,"verified"))

    conn.execute("""
        INSERT INTO yelp_listings (venue_id, city, yelp_hours_mon, yelp_hours_tue,
            yelp_hours_wed, yelp_hours_thu, yelp_hours_fri, yelp_hours_sat,
            yelp_hours_sun, last_activity_date, page_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (vid,"london","10:00-18:00","10:00-18:00","10:00-18:00","10:00-18:00",
          "10:00-21:00","10:00-18:00","11:00-18:00","2025-06-01","verified"))

    conn.execute("""
        INSERT INTO official_site_docs (venue_id, city, url, hours_mon, hours_fri,
            ticket_availability, page_status)
        VALUES (?,?,?,?,?,?,?)
    """, (vid,"london","https://tate.org.uk","10:00-18:00","10:00-21:00","{}","verified"))

    conn.execute("""
        INSERT INTO tags (tag, city, venue_id, yelp_visible)
        VALUES (?,?,?,?)
    """, ("free-entry","london",vid,1))

    doc_id = new_doc_id()
    conn.execute("""
        INSERT INTO source_docs (doc_id, city, doc_type, title, author,
            source_name, date, body, page_status)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (doc_id,"london","blog","Visiting Tate Modern","Jane","TimeOut",
          "2025-05-01","The Tate Modern is one of London's finest free museums.","verified"))

    conn.execute("INSERT INTO doc_venue_refs (doc_id, venue_id) VALUES (?,?)", (doc_id,vid))
    conn.commit()
    conn.close()
    return db, vid


db, tate_id = make_test_db()

# Patch mock_tools to use our test DB
import server.mock_tools as mt
mt._city_cache.clear()
# Force _get_db_path to return our test DB for "london" lookups
_orig_get_db = mt._get_db_path
mt._get_db_path = lambda city: db if city == "london" else _orig_get_db(city)

idx = mt._load_city_from_db("london", db)
mt._build_indexes(idx)
check("SQLite loader finds london venues", len(idx.yelp) == 1)
check("Tate Modern in yelp index", tate_id in idx.yelp)
check("Tate Modern name correct", idx.yelp[tate_id]["name"] == "Tate Modern")
check("Tate Modern has free-entry tag in category_tags", "free-entry" in idx.yelp[tate_id].get("category_tags",[]))
check("Tate Modern hours loaded", idx.yelp[tate_id].get("hours_registered",{}).get("mon") is not None)
check("Blog post loaded", len(idx.posts) == 1)
check("Blog content contains Tate", "Tate" in (idx.posts[0].get("content","") or ""))
check("Official site loaded", tate_id in idx.official)
check("Official site has url", idx.official[tate_id].get("url","").startswith("https"))
check("Official site has full_regulations", "wheelchair_accessible" in idx.official[tate_id].get("full_regulations",{}))
check("Official site full_labels includes free-entry", "free-entry" in idx.official[tate_id].get("full_labels",[]))
check("Post indexed for search", idx.post_idx is not None)
check("Yelp indexed for search", idx.yelp_idx is not None)


# ─────────────────────────────────────────────────────────────────────────────
# [2] get_official_site with date parameter
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] get_official_site date parameter")

mt._city_cache.clear()

# Without date — base response
result = mt.tool_get_official_site(tate_id, "london")
check("base response has_official_site=True", result.get("has_official_site") is True)
check("base response has hours", "hours" in result)
check("base response has no queried_date", "queried_date" not in result)

# With date — enriched response
result_date = mt.tool_get_official_site(tate_id, "london", date="2026-08-23")
check("date response has queried_date", result_date.get("queried_date") == "2026-08-23")
check("date response still has hours", "hours" in result_date)
check("date response has ticket_availability", "ticket_availability" in result_date)

# With hours_override in DB
conn = get_connection(db)
conn.execute("""
    INSERT INTO hours_overrides (venue_id, date, override_type, hours, reason)
    VALUES (?,?,?,?,?)
""", (tate_id,"2026-12-25","closed",None,"Christmas Day"))
conn.commit()
conn.close()
mt._city_cache.clear()

result_xmas = mt.tool_get_official_site(tate_id, "london", date="2026-12-25")
check("Christmas Day shows as closed", result_xmas.get("date_note","").startswith("CLOSED"))

# Unknown venue
result_bad = mt.tool_get_official_site("bad_id", "london")
check("unknown venue returns error", "error" in result_bad)

# Restore the original _get_db_path resolver
mt._get_db_path = _orig_get_db
mt._city_cache.clear()
db.unlink()


# Sections [3]–[7] (Paris JSON backward-compat, schema-from-absolute-path,
# par_medium_002 C-gate test, regression_test subprocess) were retired
# alongside the Paris-JSON pipeline. SQLite coverage above (sections [1]
# and [2]) replaces them. Evaluator C-gate behaviour is covered by
# scripts/generation/test_e3.py / test_b4.py against real London tasks.


# ─────────────────────────────────────────────────────────────────────────────
# [3] P6-T12 — Babel-backed currency lookup (replaces hardcoded CITY_CURRENCY)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] P6-T12 — Babel-backed currency lookup")

from scripts.generation.pool_utils import (
    get_city_currency, format_money, CITY_COUNTRY, _HAS_BABEL,
)

# Every city in CITY_COUNTRY must resolve to a non-USD currency (except US
# cities which legitimately map to USD). 20 cities total.
_expected_codes = {
    "london": "GBP", "paris": "EUR", "tokyo": "JPY", "istanbul": "TRY",
    "new_york": "USD", "san_francisco": "USD", "berlin": "EUR",
    "rome": "EUR", "barcelona": "EUR", "sydney": "AUD",
    "singapore": "SGD", "hong_kong": "HKD", "seoul": "KRW",
    "bangkok": "THB", "dubai": "AED", "mumbai": "INR",
    "mexico_city": "MXN", "buenos_aires": "ARS", "rio": "BRL",
    "cairo": "EGP",
}
for _city, _code in _expected_codes.items():
    check(f"P6-T12: get_city_currency('{_city}') == '{_code}'",
          get_city_currency(_city) == _code,
          f"got {get_city_currency(_city)!r}")

# Unknown city → USD with warning
check("P6-T12: unknown city → USD",
      get_city_currency("atlantis") == "USD")

# format_money returns symbol + whole-number amount
_fm_london = format_money(47.5, "london")
check("P6-T12: format_money(47.5, 'london') uses £ symbol",
      _fm_london.startswith("£"), _fm_london)
check("P6-T12: format_money rounds to whole number",
      _fm_london == "£48", _fm_london)

# CITY_COUNTRY covers exactly 20 cities (cap on additions before refactor)
check("P6-T12: CITY_COUNTRY has 20 cities", len(CITY_COUNTRY) == 20, len(CITY_COUNTRY))

# Babel availability informational
check("P6-T12: _HAS_BABEL flag is bool", isinstance(_HAS_BABEL, bool))


# ─────────────────────────────────────────────────────────────────────────────
# [4] P6-T1b — _query_pool cuisine filter
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] P6-T1b — _query_pool cuisine filter")

from scripts.generation.pool_utils import _query_pool

_t1b_pool = [
    {"venue_id": "r1", "name": "Tratt. A", "category": "restaurant", "cuisine": "italian"},
    {"venue_id": "r2", "name": "Ramen B",  "category": "restaurant", "cuisine": "japanese"},
    {"venue_id": "r3", "name": "Spice C",  "category": "restaurant", "cuisine": "indian"},
    {"venue_id": "r4", "name": "Pasta D",  "category": "restaurant", "cuisine": "italian"},
    {"venue_id": "c1", "name": "Cafe X",   "category": "cafe", "cuisine": None},
]
italian = _query_pool({"cuisine": "italian"}, _t1b_pool)
check("P6-T1b: cuisine=italian returns 2 venues",
      len(italian) == 2 and {v["venue_id"] for v in italian} == {"r1", "r4"},
      str(italian))
japanese = _query_pool({"cuisine": "japanese"}, _t1b_pool)
check("P6-T1b: cuisine=japanese returns 1 venue",
      len(japanese) == 1 and japanese[0]["venue_id"] == "r2",
      str(japanese))
none_match = _query_pool({"cuisine": "atlantis-cuisine"}, _t1b_pool)
check("P6-T1b: unknown cuisine returns 0", len(none_match) == 0)
# Combined filter: cuisine + category
combo = _query_pool({"cuisine": "italian", "category": "restaurant"}, _t1b_pool)
check("P6-T1b: cuisine + category AND filter", len(combo) == 2)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B1 tests passed ({PASS}/{total}) — tool infrastructure ready")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
