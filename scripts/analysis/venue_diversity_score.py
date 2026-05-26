"""
scripts/analysis/venue_diversity_score.py

Venue saturation analysis — how much genuine diversity does each additional
venue add to a city's pool?

"Genuine diversity" = introduces a new (category, district, traffic_tier)
combination not already present in the pool.

Usage:
  python scripts/analysis/venue_diversity_score.py --city london
  python scripts/analysis/venue_diversity_score.py --city london --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, get_city_db_path


def analyse_venue_diversity(city: str, db_path: Path = None) -> dict:
    """
    Compute venue diversity metrics for a city.

    Returns dict with:
      venues          — list of venue dicts in insertion order
      combination_curve — list of (n, n_combos, marginal_new, tag_diversity)
      saturation_point  — venue index where rolling new rate drops below 30%
      recommendation    — suggested max venue count
    """
    if db_path is None:
        db_path = get_city_db_path(city)

    conn = get_connection(db_path)

    # Load venues in rowid order (proxy for insertion/generation order)
    rows = conn.execute("""
        SELECT v.venue_id, v.name, v.category, v.district, v.traffic_tier,
               GROUP_CONCAT(t.tag) as tags
        FROM venues v
        LEFT JOIN tags t ON t.venue_id = v.venue_id
        WHERE v.city = ? AND v.page_status = 'verified'
        GROUP BY v.venue_id
        ORDER BY v.rowid
    """, (city,)).fetchall()
    conn.close()

    if not rows:
        return {"error": f"No verified venues found for '{city}'"}

    seen_combos: set[tuple] = set()
    seen_tags:   set[str]   = set()
    curve = []

    for i, row in enumerate(rows, 1):
        combo = (row["category"] or "?",
                 row["district"] or "?",
                 row["traffic_tier"] or "?")
        is_new = combo not in seen_combos
        seen_combos.add(combo)

        tag_list = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
        seen_tags.update(tag_list)

        curve.append({
            "n":            i,
            "venue_id":     row["venue_id"],
            "name":         row["name"],
            "combo":        combo,
            "is_new_combo": is_new,
            "n_combos":     len(seen_combos),
            "tag_diversity": len(seen_tags),
        })

    # Rolling marginal new rate (window of 10)
    WINDOW = 10
    for i, point in enumerate(curve):
        window_start = max(0, i - WINDOW + 1)
        window       = curve[window_start:i + 1]
        point["rolling_new_rate"] = sum(1 for p in window if p["is_new_combo"]) / len(window)

    # Find saturation point — first venue where rolling rate drops below 30%
    # and stays below for 5+ consecutive venues
    saturation_point = None
    below_count = 0
    for point in curve:
        if point["rolling_new_rate"] < 0.30:
            below_count += 1
            if below_count >= 5 and saturation_point is None:
                saturation_point = point["n"] - 4  # first of the 5
        else:
            below_count = 0

    total = len(rows)
    recommendation = saturation_point or total
    # Round up to nearest 5 for a clean target
    recommendation = (recommendation // 5 + 1) * 5 if recommendation % 5 != 0 else recommendation

    return {
        "city":            city,
        "total_venues":    total,
        "unique_combos":   len(seen_combos),
        "tag_diversity":   len(seen_tags),
        "saturation_point": saturation_point,
        "recommendation":  min(recommendation, total),
        "curve":           curve,
    }


def print_report(result: dict) -> None:
    if "error" in result:
        print(f"Error: {result['error']}")
        return

    city     = result["city"]
    total    = result["total_venues"]
    combos   = result["unique_combos"]
    tags     = result["tag_diversity"]
    sat      = result["saturation_point"]
    rec      = result["recommendation"]
    curve    = result["curve"]

    print(f"\n{'='*60}")
    print(f"VENUE DIVERSITY — {city.upper()} ({total} venues)")
    print(f"{'='*60}")
    print(f"  Unique (category, district, tier) combinations: {combos}/{total}")
    print(f"  Tag diversity: {tags} distinct tags")
    print(f"  Saturation point: {'venue ~' + str(sat) if sat else 'not reached'}")
    print(f"  Recommendation: up to ~{rec} venues for this city profile")

    # Decile breakdown
    print(f"\n  Marginal new-combination rate by decile:")
    decile = max(1, total // 10)
    for start in range(0, total, decile):
        end     = min(start + decile, total)
        segment = curve[start:end]
        new_pct = sum(1 for p in segment if p["is_new_combo"]) / len(segment)
        bar     = "█" * int(new_pct * 20)
        print(f"    Venues {start+1:3d}-{end:3d}: {new_pct:4.0%}  {bar}")

    # Final rolling rate
    final_rate = curve[-1]["rolling_new_rate"] if curve else 0
    print(f"\n  Rolling new-rate at venue {total}: {final_rate:.0%}")

    # Category / district / tier distribution
    from collections import Counter
    cats  = Counter(p["combo"][0] for p in curve)
    dists = Counter(p["combo"][1] for p in curve)
    tiers = Counter(p["combo"][2] for p in curve)

    print(f"\n  Category distribution:")
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"    {cat:20s}: {n}")

    print(f"\n  District distribution:")
    for dist, n in sorted(dists.items(), key=lambda x: -x[1])[:10]:
        print(f"    {dist:20s}: {n}")

    print(f"\n  Traffic tier distribution:")
    for tier, n in sorted(tiers.items(), key=lambda x: -x[1]):
        print(f"    {tier:10s}: {n}")


def write_csv(result: dict, csv_path: Path) -> None:
    if "error" in result or not result.get("curve"):
        return
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "n", "venue_id", "name", "category", "district", "tier",
            "is_new_combo", "n_combos", "tag_diversity", "rolling_new_rate"
        ])
        writer.writeheader()
        for p in result["curve"]:
            writer.writerow({
                "n":               p["n"],
                "venue_id":        p["venue_id"],
                "name":            p["name"],
                "category":        p["combo"][0],
                "district":        p["combo"][1],
                "tier":            p["combo"][2],
                "is_new_combo":    int(p["is_new_combo"]),
                "n_combos":        p["n_combos"],
                "tag_diversity":   p["tag_diversity"],
                "rolling_new_rate": round(p["rolling_new_rate"], 3),
            })
    print(f"\n  CSV written: {csv_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Venue diversity saturation analysis")
    parser.add_argument("--city",  required=True)
    parser.add_argument("--run-name", default=None,
                        help="Run name for separate DB (e.g. test_30). Default: production DB.")
    parser.add_argument("--db",    type=Path, default=None)
    parser.add_argument("--csv",   type=Path, default=None,
                        help="Write per-venue curve to CSV file")
    args = parser.parse_args()

    result = analyse_venue_diversity(
        args.city,
        db_path=args.db or get_city_db_path(args.city, run_name=getattr(args,'run_name',None))
    )
    print_report(result)
    if args.csv:
        write_csv(result, args.csv)
