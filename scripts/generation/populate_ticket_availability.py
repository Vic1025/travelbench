"""
scripts/generation/populate_ticket_availability.py

A8 — Ticket availability generation.

For each booking_required=1 venue in a city, generates ticket availability
rows for every date within each seasonal window. Creates:
  - Normal days: available (80%), limited (15%), sold_out (5%)
  - Anchor event dates: heavily biased toward limited/sold_out
  - Price from venue's avg_cost_local (0 for free venues)

This data is returned by get_official_site(venue_id, city, date) when
a planning agent queries a specific date.

Usage:
  python scripts/generation/populate_ticket_availability.py --city london
  python scripts/generation/populate_ticket_availability.py --city london --dry-run
"""

import json
import random
import argparse
import sys
from pathlib import Path
from datetime import date as _date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path
from scripts.generation.populate_seasonal_windows import get_windows


def _date_range(start: str, end: str) -> list[str]:
    """Return all ISO date strings from start to end inclusive."""
    s = _date.fromisoformat(start)
    e = _date.fromisoformat(end)
    days = []
    current = s
    while current <= e:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _generate_slots(venue: dict, date_str: str, anchor_dates: set,
                    rng: random.Random) -> dict:
    """
    Generate ticket availability for one venue on one date.

    Returns dict with status, slots_available, sold_out, price_local, notes.
    """
    price = venue.get("avg_cost_local") or 0.0
    # Museums/attractions typically charge admission; bars/cafes/restaurants typically don't
    if venue.get("category") in ("cafe", "bar", "neighbourhood", "park"):
        price = 0.0

    is_anchor = date_str in anchor_dates

    if is_anchor:
        # Anchor event dates: mostly sold out or very limited
        roll = rng.random()
        if roll < 0.55:
            return {"status": "sold_out", "slots_available": 0,
                    "sold_out": 1, "price_local": price,
                    "notes": "Fully booked for this date"}
        elif roll < 0.85:
            slots = rng.randint(1, 8)
            return {"status": "limited", "slots_available": slots,
                    "sold_out": 0, "price_local": price,
                    "notes": f"Only {slots} slot(s) remaining"}
        else:
            slots = rng.randint(10, 25)
            return {"status": "available", "slots_available": slots,
                    "sold_out": 0, "price_local": price, "notes": None}
    else:
        # Normal dates
        roll = rng.random()
        if roll < 0.05:
            return {"status": "sold_out", "slots_available": 0,
                    "sold_out": 1, "price_local": price,
                    "notes": "Fully booked"}
        elif roll < 0.20:
            slots = rng.randint(3, 15)
            return {"status": "limited", "slots_available": slots,
                    "sold_out": 0, "price_local": price, "notes": None}
        else:
            slots = rng.randint(20, 100)
            return {"status": "available", "slots_available": slots,
                    "sold_out": 0, "price_local": price, "notes": None}


def populate_ticket_availability(city: str, dry_run: bool = False,
                                  seed: int = 42,
                                  db_path: Path = DB_PATH) -> dict:
    """
    Generate and store ticket availability for all booking_required venues
    across all seasonal windows for a city.

    Returns summary dict.
    """
    rng = random.Random(seed)  # deterministic for reproducibility
    conn = get_connection(db_path)

    # Get booking_required venues
    venues = conn.execute("""
        SELECT venue_id, name, category, avg_cost_local
        FROM venues
        WHERE city = ? AND booking_required = 1 AND page_status = 'verified'
    """, (city,)).fetchall()

    if not venues:
        conn.close()
        return {"city": city, "status": "no_bookable_venues", "rows": 0}

    # Get windows for this city
    windows = get_windows(city, db_path=db_path)
    if not windows:
        conn.close()
        return {"city": city, "status": "no_windows", "rows": 0}

    # Collect all anchor event dates across all windows
    all_anchor_dates = set()
    for w in windows:
        for e in w.get("anchor_events", []):
            all_anchor_dates.add(e["date"])

    total_rows = 0
    rows_to_insert = []

    for v in venues:
        venue = dict(v)
        vid = venue["venue_id"]

        for window in windows:
            dates = window.get("dates", [])
            if not dates:
                continue

            for date_str in dates:
                slot = _generate_slots(venue, date_str, all_anchor_dates, rng)
                rows_to_insert.append((
                    vid, date_str, slot["status"],
                    slot["slots_available"], slot["sold_out"],
                    slot["price_local"], slot["notes"]
                ))
                total_rows += 1

    if not dry_run:
        conn.execute(
            "DELETE FROM ticket_availability WHERE venue_id IN "
            f"(SELECT venue_id FROM venues WHERE city = ?)", (city,)
        )
        conn.executemany("""
            INSERT OR REPLACE INTO ticket_availability
                (venue_id, date, status, slots_available, sold_out, price_local, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows_to_insert)
        conn.commit()

    conn.close()

    # Summary
    sold_out_count = sum(1 for r in rows_to_insert if r[4] == 1)
    limited_count  = sum(1 for r in rows_to_insert if r[2] == "limited")

    return {
        "city":        city,
        "status":      "ok",
        "venues":      len(venues),
        "windows":     len(windows),
        "rows":        total_rows,
        "sold_out":    sold_out_count,
        "limited":     limited_count,
        "anchor_dates": sorted(all_anchor_dates),
    }


def print_summary(result: dict):
    print(f"\nTicket availability — {result['city']}")
    if result["status"] != "ok":
        print(f"  Status: {result['status']}")
        return
    print(f"  Bookable venues: {result['venues']}")
    print(f"  Windows:         {result['windows']}")
    print(f"  Total rows:      {result['rows']}")
    print(f"  Sold-out slots:  {result['sold_out']}")
    print(f"  Limited slots:   {result['limited']}")
    print(f"  Anchor dates:    {', '.join(result['anchor_dates'])}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Populate ticket availability (A8)"
    )
    parser.add_argument("--city",    required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed",    type=int, default=42)
    parser.add_argument("--db",      type=Path, default=DB_PATH)
    args = parser.parse_args()

    result = populate_ticket_availability(
        args.city, dry_run=args.dry_run,
        seed=args.seed, db_path=args.db
    )
    print_summary(result)
    if args.dry_run:
        print("\n(dry-run — nothing written to DB)")
    elif result["status"] == "ok":
        print(f"\n✅ Written to DB")
