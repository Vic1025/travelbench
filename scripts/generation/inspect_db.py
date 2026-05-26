"""
scripts/generation/inspect_db.py

Human-readable view of what's in the generation DB.
No SQL knowledge required.

Usage:
  python scripts/generation/inspect_db.py                        # overview of all cities
  python scripts/generation/inspect_db.py --city london          # all venues for a city
  python scripts/generation/inspect_db.py --venue Ic0NRt8        # full detail for one venue
  python scripts/generation/inspect_db.py --city london --docs   # show source doc bodies too
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH


def overview(conn):
    cities = conn.execute("SELECT * FROM city_config").fetchall()
    if not cities:
        print("No cities in DB yet. Run generate_city_venues.py first.")
        return
    for city in cities:
        n_venues = conn.execute("SELECT COUNT(*) FROM venues WHERE city=?", (city["city"],)).fetchone()[0]
        n_docs = conn.execute("SELECT COUNT(*) FROM source_docs WHERE city=?", (city["city"],)).fetchone()[0]
        n_wi = conn.execute(
            "SELECT COUNT(*) FROM wrong_info WHERE venue_id IN "
            "(SELECT venue_id FROM venues WHERE city=?)", (city["city"],)
        ).fetchone()[0]
        print(f"\n{'='*60}")
        print(f"  {city['display_name']}, {city['country']}")
        # P6-T23: city-level task_dates retired; show seasonal windows instead.
        try:
            import json as _j
            wins = _j.loads(city['seasonal_windows'] or '[]')
        except Exception:
            wins = []
        if wins:
            print(f"  Seasonal windows ({len(wins)}):")
            for w in wins:
                ds = w.get('dates', [])
                drange = f"{ds[0]}..{ds[-1]}" if ds else "?"
                print(f"    - {w.get('window_id', '?')}: {drange}")
        else:
            print(f"  Seasonal windows: (none)")
        print(f"  Venues: {n_venues}  |  Source docs: {n_docs}  |  Wrong info entries: {n_wi}")


def show_city(conn, city, show_docs=False):
    city_row = conn.execute("SELECT * FROM city_config WHERE city=?", (city,)).fetchone()
    if not city_row:
        print(f"City '{city}' not found. Run with no args to see available cities.")
        return

    print(f"\n{'='*60}")
    print(f"  {city_row['display_name']}, {city_row['country']}")
    print(f"  Local cuisine: {city_row['local_cuisine_label']}")
    # P6-T23: show per-window dates + weather instead of vestigial task_dates
    try:
        import json as _j
        wins = _j.loads(city_row['seasonal_windows'] or '[]')
    except Exception:
        wins = []
    if wins:
        print(f"  Seasonal windows ({len(wins)}):")
        for w in wins:
            ds = w.get('dates', [])
            drange = f"{ds[0]}..{ds[-1]}" if ds else "?"
            wn = w.get('weather_notes', '').strip()
            wn_str = f" — {wn}" if wn else ""
            print(f"    - {w.get('window_id', '?')}: {drange}{wn_str}")

    venues = conn.execute(
        "SELECT * FROM venues WHERE city=? ORDER BY category, district", (city,)
    ).fetchall()
    print(f"\n  {len(venues)} venues:\n")

    for v in venues:
        v = dict(v)
        wi = conn.execute(
            "SELECT COUNT(*) FROM wrong_info WHERE venue_id=?", (v["venue_id"],)
        ).fetchone()[0]
        tags = conn.execute(
            "SELECT tag FROM tags WHERE venue_id=? AND yelp_visible=1", (v["venue_id"],)
        ).fetchall()
        tag_str = ", ".join(t["tag"] for t in tags) or "—"
        wi_str = f"  ⚠ {wi} wrong info" if wi else ""
        print(f"  [{v['category']:12s}] {v['name']:40s} {v['district']:15s} "
              f"tier={v['traffic_tier']}{wi_str}")
        print(f"               tags: {tag_str}")

        if show_docs:
            docs = conn.execute(
                "SELECT s.doc_type, s.title, s.date, s.body "
                "FROM source_docs s JOIN doc_venue_refs r ON s.doc_id=r.doc_id "
                "WHERE r.venue_id=?", (v["venue_id"],)
            ).fetchall()
            for d in docs:
                print(f"               [{d['doc_type']:6s}] {d['title'][:50]} ({d['date']})")
                print(f"                       {d['body'][:120]}...")
        print()


def show_venue(conn, venue_id):
    v = conn.execute("SELECT * FROM venues WHERE venue_id=?", (venue_id,)).fetchone()
    if not v:
        print(f"Venue '{venue_id}' not found.")
        return
    v = dict(v)

    print(f"\n{'='*60}")
    print(f"  {v['name']}  [{v['category']}]  {v['district']}, {v['city']}")
    print(f"  {v.get('address', 'no address')}")
    print(f"  venue_id: {v['venue_id']}  |  traffic: {v['traffic_tier']}  |  pace: {v['recommended_pace']}")
    print(f"  price: {v['price_tier']} (~${v['avg_cost_local']}/person)")
    print(f"  visit: ~{v['recommended_visit_minutes']} min  |  outdoor: {v['outdoor_sensitivity']}")
    print(f"  noise: {v['noise_level']}  |  booking: {'required' if v['booking_required'] else 'not required'}")

    print(f"\n  Hours:")
    days = ["mon","tue","wed","thu","fri","sat","sun"]
    for day in days:
        h = v.get(f"hours_{day}")
        print(f"    {day}: {h or 'closed'}")

    print(f"\n  Regulations:")
    regs = ["pet_friendly","wheelchair_accessible","photography_allowed",
            "reservation_required","outside_food_allowed","food_available"]
    for r in regs:
        val = v.get(r)
        if val is not None:
            print(f"    {r}: {bool(val)}")
    if v.get("age_restriction"):
        print(f"    age_restriction: {v['age_restriction']}+")
    if v.get("dress_code"):
        print(f"    dress_code: {v['dress_code']}")

    # Tags
    all_tags = conn.execute("SELECT tag, yelp_visible FROM tags WHERE venue_id=?", (venue_id,)).fetchall()
    yelp_tags = [t["tag"] for t in all_tags if t["yelp_visible"]]
    hidden_tags = [t["tag"] for t in all_tags if not t["yelp_visible"]]
    print(f"\n  Tags:")
    print(f"    Yelp visible: {yelp_tags}")
    print(f"    Hidden:       {hidden_tags}")

    # Yelp listing
    yelp = conn.execute("SELECT * FROM yelp_listings WHERE venue_id=?", (venue_id,)).fetchone()
    if yelp:
        print(f"\n  Yelp listing: ★{yelp['stars']} ({yelp['review_count']} reviews)")
        print(f"    \"{yelp['top_review_snippet']}\"")
        yelp_days = {day: dict(yelp).get(f"yelp_hours_{day}") for day in days}
        venue_days = {day: v.get(f"hours_{day}") for day in days}
        diffs = {day for day in days if yelp_days[day] != venue_days[day] and yelp_days[day]}
        if diffs:
            print(f"    ⚠ Stale hours on: {', '.join(diffs)}")
            for day in diffs:
                print(f"      {day}: yelp={yelp_days[day]}  truth={venue_days[day]}")

    # Wrong info
    wi_rows = conn.execute("SELECT * FROM wrong_info WHERE venue_id=?", (venue_id,)).fetchall()
    if wi_rows:
        print(f"\n  Wrong info ({len(wi_rows)} entries):")
        for wi in wi_rows:
            print(f"    field={wi['affected_field']}  "
                  f"incorrect='{wi['incorrect_value']}'  correct='{wi['correct_value']}'")
            print(f"    via {wi['source_type']} | {wi['wrong_info_category']}")
            print(f"    origin: {wi['origin_story']}")

    # Source docs
    docs = conn.execute(
        "SELECT s.*, r.role FROM source_docs s "
        "JOIN doc_venue_refs ref ON s.doc_id=ref.doc_id "
        "LEFT JOIN doc_venue_roles r ON r.doc_id=s.doc_id AND r.venue_id=? "
        "WHERE ref.venue_id=?", (venue_id, venue_id)
    ).fetchall()
    if docs:
        print(f"\n  Source docs ({len(docs)}):")
        for d in docs:
            role_str = f" [{d['role']}]" if d["role"] else ""
            print(f"    [{d['doc_type']:6s}]{role_str} {d['title'][:55]}  ({d['date']})")
            print(f"             likes={d['likes']}  {d['body'][:120]}...")
            if d["mentioned_regulations"]:
                print(f"             mentions: {d['mentioned_regulations']}")

    # Official site
    official = conn.execute("SELECT * FROM official_site_docs WHERE venue_id=?", (venue_id,)).fetchone()
    if official:
        print(f"\n  Official site: {official['url']}")
        print(f"    {official['body'][:150]}...")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect TravelBench generation DB")
    parser.add_argument("--city", default=None, help="Show all venues for a city")
    parser.add_argument("--venue", default=None, help="Show full detail for a venue_id")
    parser.add_argument("--docs", action="store_true", help="Include source doc bodies")
    parser.add_argument("--db", default=None, help="Path to DB file (default: data/travelbench.db)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"DB not found at {db_path}")
        print("Run with --keep-db flag during venue generation to persist the DB.")
        sys.exit(1)

    conn = get_connection(db_path)

    if args.venue:
        show_venue(conn, args.venue)
    elif args.city:
        show_city(conn, args.city, show_docs=args.docs)
    else:
        overview(conn)

    conn.close()
