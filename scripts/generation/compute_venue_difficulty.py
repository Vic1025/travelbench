"""
scripts/generation/compute_venue_difficulty.py

Computes venue_difficulty_score (0.0–1.0) for each venue in a city.

Difficulty reflects how hard it is for a planning agent to find
reliable information about a venue. High difficulty = agent is more
likely to encounter wrong info, sparse sources, or correction chains.

Factors (all pure arithmetic, no LLM):
  1. Information scarcity: low total_results → harder to find reliable info
  2. Source doc count: fewer docs → less cross-referencing possible
  3. Wrong info presence: has wrong info → planning trap exists
  4. Correction availability: truth carrier registered → trap is detectable
     (paradoxically, detectable traps are slightly harder — agent must find them)
  5. Traffic tier: low-traffic venues are inherently harder to research

Formula:
  base = 1.0 - normalise(total_results, 0, 50000)   # scarcity
  + 0.2 if has_wrong_info
  + 0.1 if truth_carrier_registered (detectable trap)
  - 0.1 if total_results > 10000 and not has_wrong_info  (well-documented, clean)
  + 0.15 if traffic_tier == "low"
  + 0.05 if traffic_tier == "mid"
  Clamped to [0.0, 1.0]

Usage:
  python scripts/generation/compute_venue_difficulty.py --city london
  python scripts/generation/compute_venue_difficulty.py --city london --dry-run
"""

import json
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path


def _normalise(value: float, lo: float, hi: float) -> float:
    """Normalise value to [0, 1] within [lo, hi]."""
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def compute_venue_difficulty(venue: dict, doc_count: int,
                              has_truth_carrier: bool) -> float:
    """
    Compute difficulty score for a single venue.

    Args:
        venue: venue row dict from DB
        doc_count: number of source docs referencing this venue
        has_truth_carrier: whether at least one wrong_info entry has a
                           registered truth carrier doc

    Returns float in [0.0, 1.0]
    """
    total_results   = venue.get("total_results", 0) or 0
    traffic_tier    = venue.get("traffic_tier", "mid")
    has_wrong_info  = bool(venue.get("has_wrong_info_planned", 0))

    # Base: information scarcity (0 = very findable, 1 = very obscure)
    # Normalise total_results against 50k (typical high-traffic ceiling)
    scarcity = 1.0 - _normalise(total_results, 0, 50000)
    score    = scarcity * 0.5  # scarcity contributes up to 0.5

    # Traffic tier modifier
    if traffic_tier == "low":
        score += 0.15
    elif traffic_tier == "mid":
        score += 0.05
    # high: no modifier — already reflected in total_results

    # Source doc count: fewer docs → harder
    if doc_count == 0:
        score += 0.15
    elif doc_count == 1:
        score += 0.10
    elif doc_count == 2:
        score += 0.05
    # 3+ docs: well-documented, no penalty

    # Wrong info presence
    if has_wrong_info:
        score += 0.20
        # Truth carrier registered: trap is detectable but requires effort
        if has_truth_carrier:
            score += 0.05
    else:
        # Clean, well-documented venue is easier
        if total_results > 10000:
            score -= 0.10

    return round(max(0.0, min(1.0, score)), 3)


def compute_all_venue_difficulties(city: str, dry_run: bool = False,
                                    db_path: Path = None) -> dict:
    """
    Compute and optionally write difficulty scores for all venues in a city.

    Returns dict: {venue_id: difficulty_score}
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    conn = get_connection(db_path)

    venues = conn.execute(
        "SELECT * FROM venues WHERE city = ?", (city,)
    ).fetchall()

    if not venues:
        conn.close()
        return {}

    scores = {}

    for row in venues:
        v   = dict(row)
        vid = v["venue_id"]

        # Count source docs
        doc_count = conn.execute(
            "SELECT COUNT(DISTINCT s.doc_id) FROM source_docs s "
            "JOIN doc_venue_refs r ON s.doc_id = r.doc_id "
            "WHERE r.venue_id = ?", (vid,)
        ).fetchone()[0]

        # Check truth carrier registration
        has_wrong_info = bool(v.get("has_wrong_info_planned", 0))
        has_truth_carrier = False
        if has_wrong_info:
            tc = conn.execute(
                "SELECT 1 FROM wrong_info wi "
                "JOIN doc_venue_roles dvr ON wi.wrong_info_id = dvr.wrong_info_id "
                "WHERE wi.venue_id = ? AND dvr.role = 'truth_carrier'",
                (vid,)
            ).fetchone()
            has_truth_carrier = tc is not None

        difficulty = compute_venue_difficulty(v, doc_count, has_truth_carrier)
        scores[vid] = difficulty

        if not dry_run:
            conn.execute(
                "UPDATE venues SET venue_difficulty_score = ? WHERE venue_id = ?",
                (difficulty, vid)
            )

    if not dry_run:
        conn.commit()

    conn.close()
    return scores


def print_difficulty_report(city: str, scores: dict, db_path: Path = None):
    """Print a human-readable difficulty distribution report."""
    if not scores:
        print(f"No scores for {city}")
        return

    if db_path is None:
        db_path = get_city_db_path(city)
    conn = get_connection(db_path)
    venue_names = {
        row["venue_id"]: row["name"]
        for row in conn.execute(
            "SELECT venue_id, name FROM venues WHERE city = ?", (city,)
        ).fetchall()
    }
    conn.close()

    vals = list(scores.values())
    print(f"\nVenue difficulty scores — {city} ({len(scores)} venues)")
    print(f"  Mean:   {sum(vals)/len(vals):.3f}")
    print(f"  Min:    {min(vals):.3f}")
    print(f"  Max:    {max(vals):.3f}")

    bands = {"easy (0.0-0.3)": 0, "medium (0.3-0.6)": 0, "hard (0.6-1.0)": 0}
    for s in vals:
        if s < 0.3:   bands["easy (0.0-0.3)"] += 1
        elif s < 0.6: bands["medium (0.3-0.6)"] += 1
        else:         bands["hard (0.6-1.0)"] += 1
    for band, count in bands.items():
        print(f"  {band}: {count}")

    print("\n  Top 5 hardest:")
    for vid, s in sorted(scores.items(), key=lambda x: -x[1])[:5]:
        print(f"    {venue_names.get(vid, vid)[:40]:40s} {s:.3f}")

    print("\n  Top 5 easiest:")
    for vid, s in sorted(scores.items(), key=lambda x: x[1])[:5]:
        print(f"    {venue_names.get(vid, vid)[:40]:40s} {s:.3f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Compute venue difficulty scores"
    )
    parser.add_argument("--city",    required=True)
    parser.add_argument("--run-name", default=None,
                        help="Run name for separate DB (e.g. test_30). Default: production DB.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute but don't write to DB")
    parser.add_argument("--db",      type=Path, default=DB_PATH)
    args = parser.parse_args()

    scores = compute_all_venue_difficulties(
        args.city, dry_run=args.dry_run, db_path=args.db or get_city_db_path(args.city, run_name=getattr(args,'run_name',None))
    )
    print_difficulty_report(args.city, scores, db_path=args.db or get_city_db_path(args.city, run_name=getattr(args,'run_name',None)))

    if args.dry_run:
        print("\n(dry-run — scores not written to DB)")
    else:
        print(f"\n✅ Scores written to DB for {len(scores)} venues")
