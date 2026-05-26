#!/usr/bin/env python3
"""
Backfill yelp_listings hours from venue ground truth.

For any venue where yelp_hours_* is NULL but venues.hours_* is set,
copies the ground truth hours to yelp. Then applies wrong_info overwrites
for venues with hours-related wrong_info entries.

Usage:
  python scripts/generation/backfill_yelp_hours.py --city london --run-name test_50
"""
import argparse
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.generation.db import get_city_db_path, get_connection


DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def backfill_yelp_hours(city: str, db_path: Path = None, dry_run: bool = False):
    """Copy venue hours → yelp hours where yelp is null, then apply wrong_info."""
    if db_path is None:
        db_path = get_city_db_path(city)

    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row

    # Find venues with ground truth hours but null yelp hours
    rows = conn.execute("""
        SELECT v.venue_id, v.name, v.category,
               v.hours_mon, v.hours_tue, v.hours_wed, v.hours_thu,
               v.hours_fri, v.hours_sat, v.hours_sun,
               y.yelp_hours_mon, y.yelp_hours_tue, y.yelp_hours_wed,
               y.yelp_hours_thu, y.yelp_hours_fri, y.yelp_hours_sat,
               y.yelp_hours_sun
        FROM venues v
        JOIN yelp_listings y ON v.venue_id = y.venue_id
        WHERE v.page_status = 'verified'
    """).fetchall()

    backfilled = 0
    wrong_info_applied = 0

    for row in rows:
        r = dict(row)
        vid = r["venue_id"]

        # Check if yelp has any hours set
        yelp_has_hours = any(r.get(f"yelp_hours_{d}") is not None for d in DAYS)
        venue_has_hours = any(r.get(f"hours_{d}") is not None for d in DAYS)

        if yelp_has_hours or not venue_has_hours:
            continue  # already has yelp hours or venue has no hours

        # Copy venue hours → yelp hours
        updates = {}
        for d in DAYS:
            gt_val = r.get(f"hours_{d}")
            if gt_val is not None:
                updates[f"yelp_hours_{d}"] = gt_val
            else:
                updates[f"yelp_hours_{d}"] = "Closed"

        if dry_run:
            print(f"  [dry] {r['name']:35s} → would set {len(updates)} yelp hours")
        else:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE yelp_listings SET {set_clause} WHERE venue_id = ?",
                list(updates.values()) + [vid]
            )
            backfilled += 1
            print(f"  ✓ {r['name']:35s} → copied {len(updates)} hours from ground truth")

    # Apply wrong_info overwrites
    wi_rows = conn.execute("""
        SELECT w.venue_id, v.name, w.affected_field, w.incorrect_value
        FROM wrong_info w
        JOIN venues v ON w.venue_id = v.venue_id
        WHERE w.source_type = 'yelp'
          AND w.affected_field LIKE 'hours_%'
          AND v.page_status = 'verified'
    """).fetchall()

    for wi in wi_rows:
        vid = wi["venue_id"]
        field = wi["affected_field"]  # e.g. "hours_fri"
        yelp_field = f"yelp_{field}"  # e.g. "yelp_hours_fri"
        incorrect = wi["incorrect_value"]

        if dry_run:
            print(f"  [dry] {wi['name']:35s} → would set {yelp_field}={incorrect} (wrong_info)")
        else:
            conn.execute(
                f"UPDATE yelp_listings SET {yelp_field} = ? WHERE venue_id = ?",
                (incorrect, vid)
            )
            wrong_info_applied += 1
            print(f"  ⚠ {wi['name']:35s} → {yelp_field}={incorrect} (wrong_info override)")

    if not dry_run:
        conn.commit()

    conn.close()
    print(f"\nDone: {backfilled} venues backfilled, {wrong_info_applied} wrong_info overwrites applied")
    return backfilled, wrong_info_applied


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill yelp hours from venue ground truth")
    parser.add_argument("--city", required=True)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = get_city_db_path(args.city, run_name=args.run_name)
    if not db.exists():
        print(f"❌ DB not found: {db}")
        sys.exit(1)

    print(f"Backfilling yelp hours for {args.city} (DB: {db})")
    backfill_yelp_hours(args.city, db_path=db, dry_run=args.dry_run)
