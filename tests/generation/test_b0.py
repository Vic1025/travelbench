"""
scripts/generation/test_b0.py

Tests for B0 — seasonal windows schema and PLAN_VENUES window injection.
"""

import sys, tempfile, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import init_db, get_connection
from scripts.generation.populate_seasonal_windows import (
    populate_windows, get_windows, get_window,
    generate_and_store_windows,
    LONDON_WINDOWS, HOKKAIDO_WINDOWS, RIO_WINDOWS, CITY_WINDOWS
)
from scripts.generation.generate_city_venues import plan_venues

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
    """, (city, city.title(), "XX", 0.0, 0.0, 5.0, "local", "[]"))
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# [1] Window data integrity
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] Window data integrity")

for city, windows in CITY_WINDOWS.items():
    for w in windows:
        wid = w["window_id"]
        check(f"{wid}: has 7 dates", len(w["dates"]) == 7, len(w["dates"]))
        check(f"{wid}: has anchor_events", len(w.get("anchor_events", [])) >= 1)
        check(f"{wid}: has character", len(w.get("character", "")) > 50)
        check(f"{wid}: has 3 wrong_info_hints",
              len(w.get("conditional_wrong_info_hints", [])) == 3)
        hint_types = {h["type"] for h in w.get("conditional_wrong_info_hints", [])}
        check(f"{wid}: hints cover hours/access/character traps",
              hint_types == {"hours_trap", "access_trap", "character_trap"})
        check(f"{wid}: dates are 2026",
              all(d.startswith("2026") for d in w["dates"]))

# Count totals
check("London has 4 windows", len(LONDON_WINDOWS) == 4)
check("Hokkaido has 2 windows", len(HOKKAIDO_WINDOWS) == 2)
check("Rio has 3 windows", len(RIO_WINDOWS) == 3)

# Hokkaido pools are distinct
hok_pools = [w["venue_pool_id"] for w in HOKKAIDO_WINDOWS]
check("Hokkaido windows have distinct pool IDs", len(set(hok_pools)) == 2, hok_pools)

# London windows all use base pool
lon_pools = [w["venue_pool_id"] for w in LONDON_WINDOWS]
check("London windows all use base pool (None)", all(p is None for p in lon_pools))


# ─────────────────────────────────────────────────────────────────────────────
# [2] DB write and retrieval
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] DB write and retrieval")

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
conn.close()

result = populate_windows("london", db_path=db)
check("populate london ok", result["status"] == "ok", result)
check("4 windows written", result["windows_written"] == 4)

windows = get_windows("london", db_path=db)
check("get_windows returns 4", len(windows) == 4)

w = get_window("london", "london_carnival_2026", db_path=db)
check("get_window by id works", w is not None)
check("carnival window has correct dates",
      w["dates"][0] == "2026-08-20" and w["dates"][-1] == "2026-08-26")
check("carnival anchor has Carnival Sunday",
      any(e["name"] == "Notting Hill Carnival Sunday" for e in w["anchor_events"]))

# Unknown city
result2 = populate_windows("atlantis", db_path=db)
check("unknown city returns error", result2["status"] == "unknown_city")

# City not in DB
db2 = make_db()
result3 = populate_windows("london", db_path=db2)
check("city not in DB returns error", result3["status"] == "city_not_in_db")
db2.unlink()

db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [3] All three cities
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] All three cities write successfully")

db = make_db()
conn = get_connection(db)
for city in ["london", "hokkaido", "rio"]:
    insert_city(conn, city)
conn.close()

for city, expected_count in [("london", 4), ("hokkaido", 2), ("rio", 3)]:
    result = populate_windows(city, db_path=db)
    check(f"{city} populate ok", result["status"] == "ok")
    windows = get_windows(city, db_path=db)
    check(f"{city} has {expected_count} windows", len(windows) == expected_count)

db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# [4] _plan_prompt_with_events window injection
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] _plan_prompt_with_events window injection")

from scripts.generation.generate_city_venues import _plan_prompt_with_events

base_cfg = {
    "display_name": "London",
    "country": "United Kingdom",
    "districts": ["Soho", "Shoreditch", "Camden"],
    "cuisine_variety": ["British", "Indian", "Italian"],
    "local_cuisine_label": "British",
}

# Without window — base prompt (no events, no seasonal context)
prompt_base = _plan_prompt_with_events(base_cfg, window=None, include_events=False)
check("base prompt contains London", "London" in prompt_base)
check("base prompt has no seasonal context", "SEASONAL CONTEXT" not in prompt_base)

# With window, include_events=True — unified prompt
carnival_window = {
    "window_id": "london_carnival_2026",
    "label": "Summer — Notting Hill Carnival",
    "dates": ["2026-08-20", "2026-08-21", "2026-08-22",
              "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26"],
    "anchor_events": [
        {"name": "Notting Hill Carnival Sunday", "date": "2026-08-23", "type": "festival"},
        {"name": "Notting Hill Carnival Monday", "date": "2026-08-24", "type": "bank_holiday"},
    ],
    "character": "Europe's largest street festival transforms Notting Hill for two days.",
}

prompt_window = _plan_prompt_with_events(base_cfg, window=carnival_window, include_events=True)
check("window prompt contains SEASONAL CONTEXT", "SEASONAL CONTEXT" in prompt_window)
check("window prompt contains window label", "Summer — Notting Hill Carnival" in prompt_window)
check("window prompt contains date range", "2026-08-20" in prompt_window)
check("window prompt contains anchor events", "Notting Hill Carnival Sunday" in prompt_window)
check("window prompt contains character text", "largest street festival" in prompt_window)
check("window prompt still has POOL REQUIREMENTS", "POOL REQUIREMENTS" in prompt_window)
check("window prompt longer than base", len(prompt_window) > len(prompt_base))
check("unified prompt has PART 2 events section", "PART 2" in prompt_window)

# Hokkaido winter window — checks season-specific guidance
hokkaido_cfg = {
    "display_name": "Hokkaido",
    "country": "Japan",
    "districts": ["Sapporo", "Niseko", "Furano"],
    "cuisine_variety": ["Japanese", "Ramen", "Seafood"],
    "local_cuisine_label": "Hokkaido Japanese",
}
winter_window = {
    "window_id": "hokkaido_snow_festival_2026",
    "label": "Winter — Sapporo Snow Festival Week",
    "dates": ["2026-02-05", "2026-02-06", "2026-02-07",
              "2026-02-08", "2026-02-09", "2026-02-10", "2026-02-11"],
    "anchor_events": [{"name": "Sapporo Snow Festival", "date": "2026-02-05", "type": "festival"}],
    "character": "Ski resorts at peak, Snow Festival draws 2 million visitors.",
}
prompt_hok = _plan_prompt_with_events(hokkaido_cfg, window=winter_window, include_events=False)
check("Hokkaido winter prompt has season-specific guidance",
      "season-specific" in prompt_hok or "only include venues" in prompt_hok)


# ─────────────────────────────────────────────────────────────────────────────
# [5] plan_venues window parameter passes through (dry-run)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] plan_venues window parameter (dry-run)")

db = make_db()
conn = get_connection(db)
insert_city(conn, "london")
conn.close()

cfg_full = {**base_cfg, "city": "london", "bbox_min_lat": 51.45, "bbox_max_lat": 51.56,
            "bbox_min_lng": -0.25, "bbox_max_lng": 0.01}
briefs, _ = plan_venues(cfg_full, window=carnival_window, dry_run=True)
check("plan_venues with window dry-run succeeds", len(briefs) == 50,
      f"got {len(briefs)}")
check("briefs have expected fields",
      all("name" in b and "category" in b and "has_wrong_info" in b for b in briefs))

db.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
total = PASS + FAIL
if FAIL == 0:
    print(f"✅ All B0 tests passed ({PASS}/{total}) — seasonal windows ready")
else:
    print(f"❌ {FAIL}/{total} tests failed")
sys.exit(0 if FAIL == 0 else 1)
