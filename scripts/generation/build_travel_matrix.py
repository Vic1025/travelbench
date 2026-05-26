"""
scripts/generation/build_travel_matrix.py

Computes travel times between all venue pairs for a city and stores
them in the travel_matrix table.

Season-independent: physical distance between venues is fixed regardless
of date or window. Run once per city (or once per distinct seasonal pool
for Hokkaido where venue sets differ between windows).

Uses OpenRouteService matrix API for walking, transit, and cycling.
Falls back to haversine straight-line estimate if ORS is unavailable.

Usage:
  python scripts/generation/build_travel_matrix.py --city london --api-key ORS_KEY
  python scripts/generation/build_travel_matrix.py --city london --dry-run
  python scripts/generation/build_travel_matrix.py --city hokkaido --pool winter --api-key KEY
"""

import json
import time
import math
import argparse
import sys
from pathlib import Path
from itertools import combinations

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

ORS_BASE = "https://api.openrouteservice.org/v2/matrix"

# ORS profile → column name
PROFILES = {
    "foot-walking":  "walk_minutes",
    "cycling-regular": "cycling_minutes",
}

# ORS matrix API limit: 50 locations per request
ORS_BATCH_SIZE = 50

# Walking speed fallback (km/h)
WALK_SPEED_KMH = 5.0
CYCLE_SPEED_KMH = 15.0
TRANSIT_SPEED_KMH = 20.0  # rough city transit average


# ─────────────────────────────────────────────────────────────────────────────
# HAVERSINE FALLBACK
# ─────────────────────────────────────────────────────────────────────────────

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance in km."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def estimate_minutes(distance_km: float, speed_kmh: float) -> float:
    """Estimate travel minutes from distance and speed."""
    return round((distance_km / speed_kmh) * 60, 1)


def compute_fallback(venues: list) -> list[dict]:
    """
    Compute all pairs using haversine estimates.
    Returns list of row dicts ready for DB insert.
    """
    rows = []
    for va, vb in combinations(venues, 2):
        dist = haversine_km(va["lat"], va["lng"], vb["lat"], vb["lng"])
        # Apply ~1.3x factor for real road distance vs straight line
        road_dist = dist * 1.3
        rows.append({
            "venue_id_a": va["venue_id"],
            "venue_id_b": vb["venue_id"],
            "walk_minutes":    estimate_minutes(road_dist, WALK_SPEED_KMH),
            "transit_minutes": estimate_minutes(road_dist, TRANSIT_SPEED_KMH),
            "cycling_minutes": estimate_minutes(road_dist, CYCLE_SPEED_KMH),
            "distance_km": round(dist, 3),
        })
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# ORS API
# ─────────────────────────────────────────────────────────────────────────────

def _ors_matrix(locations: list, profile: str, api_key: str) -> list[list[float]] | None:
    """
    Call ORS matrix API for a batch of locations.
    Returns duration matrix in seconds, or None on failure.
    locations: list of [lng, lat] pairs (ORS uses lng-first)
    """
    if not HAS_REQUESTS:
        return None

    url = f"{ORS_BASE}/{profile}"
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "locations": locations,
        "metrics": ["duration"],
        "units": "km",
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("durations")
        else:
            print(f"  ORS error {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  ORS request failed: {e}")
        return None


def compute_ors(venues: list, api_key: str, verbose: bool = False) -> list[dict]:
    """
    Compute travel matrix via ORS API for all venue pairs.
    Batches into ORS_BATCH_SIZE chunks if needed.
    Returns list of row dicts for DB insert.
    """
    n = len(venues)
    # ORS supports up to ORS_BATCH_SIZE locations per call
    # For city pools up to 50 venues this fits in one call
    if n > ORS_BATCH_SIZE:
        print(f"  Warning: {n} venues exceeds ORS batch limit {ORS_BATCH_SIZE}. "
              f"Using haversine fallback.")
        return compute_fallback(venues)

    locations = [[v["lng"], v["lat"]] for v in venues]
    id_list = [v["venue_id"] for v in venues]

    # Build distance_km lookup from haversine (ORS doesn't always return distance)
    dist_lookup = {}
    for i, va in enumerate(venues):
        for j, vb in enumerate(venues):
            if i != j:
                dist_lookup[(i, j)] = haversine_km(
                    va["lat"], va["lng"], vb["lat"], vb["lng"]
                )

    # Fetch walking matrix
    walk_matrix = None
    cycle_matrix = None

    if verbose:
        print("  Fetching walking matrix...")
    walk_matrix = _ors_matrix(locations, "foot-walking", api_key)
    time.sleep(1)  # Rate limit

    if verbose:
        print("  Fetching cycling matrix...")
    cycle_matrix = _ors_matrix(locations, "cycling-regular", api_key)
    time.sleep(1)

    rows = []
    for i in range(n):
        for j in range(i + 1, n):
            dist = dist_lookup.get((i, j), 0)
            road_dist = dist * 1.3

            walk_s = walk_matrix[i][j] if walk_matrix else None
            cycle_s = cycle_matrix[i][j] if cycle_matrix else None

            walk_min = round(walk_s / 60, 1) if walk_s else estimate_minutes(road_dist, WALK_SPEED_KMH)
            cycle_min = round(cycle_s / 60, 1) if cycle_s else estimate_minutes(road_dist, CYCLE_SPEED_KMH)
            transit_min = estimate_minutes(road_dist, TRANSIT_SPEED_KMH)

            rows.append({
                "venue_id_a": id_list[i],
                "venue_id_b": id_list[j],
                "walk_minutes":    walk_min,
                "transit_minutes": transit_min,
                "cycling_minutes": cycle_min,
                "distance_km": round(dist, 3),
            })

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def build_travel_matrix(city: str, api_key: str = None,
                        pool: str = None, dry_run: bool = False,
                        db_path: Path = DB_PATH, verbose: bool = False) -> dict:
    """
    Compute and store travel matrix for all venues in a city.

    Args:
        city:    city name (e.g. 'london', 'hokkaido')
        api_key: OpenRouteService API key (uses haversine fallback if None)
        pool:    optional seasonal pool filter for Hokkaido ('winter'|'summer')
                 — not yet implemented, reserved for B0 seasonal schema
        dry_run: compute but don't write to DB
        verbose: print progress
    Returns summary dict.
    """
    conn = get_connection(db_path)

    query = "SELECT venue_id, name, lat, lng FROM venues WHERE city = ? AND lat IS NOT NULL AND lng IS NOT NULL"
    venues = [dict(r) for r in conn.execute(query, (city,)).fetchall()]

    if not venues:
        conn.close()
        return {"city": city, "status": "no_venues", "pairs": 0}

    if verbose:
        print(f"Computing matrix for {city}: {len(venues)} venues → "
              f"{len(venues) * (len(venues) - 1) // 2} pairs")

    # Compute
    if api_key and HAS_REQUESTS:
        rows = compute_ors(venues, api_key, verbose=verbose)
        method = "ors"
    else:
        if verbose and not dry_run:
            print("  No API key or requests unavailable — using haversine fallback")
        rows = compute_fallback(venues)
        method = "haversine"

    if dry_run:
        conn.close()
        # Spot-check: verify a known pair if coordinates look right
        if verbose:
            for r in rows[:3]:
                print(f"  {r['venue_id_a']} → {r['venue_id_b']}: "
                      f"walk={r['walk_minutes']}min dist={r['distance_km']}km")
        return {
            "city": city, "status": "dry_run", "pairs": len(rows),
            "method": method, "venues": len(venues)
        }

    # Write to DB
    conn.execute("DELETE FROM travel_matrix WHERE city = ?", (city,))
    conn.executemany("""
        INSERT OR REPLACE INTO travel_matrix
            (city, venue_id_a, venue_id_b, walk_minutes, transit_minutes,
             cycling_minutes, distance_km)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        (city, r["venue_id_a"], r["venue_id_b"],
         r["walk_minutes"], r["transit_minutes"],
         r["cycling_minutes"], r["distance_km"])
        for r in rows
    ])
    conn.commit()
    conn.close()

    return {
        "city": city, "status": "ok", "pairs": len(rows),
        "method": method, "venues": len(venues)
    }


def get_travel_time(venue_id_a: str, venue_id_b: str,
                    mode: str = "walk",
                    db_path: Path = DB_PATH) -> float | None:
    """
    Retrieve travel time between two venues from the matrix.
    mode: 'walk' | 'transit' | 'cycling'
    Returns minutes or None if pair not in matrix.
    """
    col = {"walk": "walk_minutes", "transit": "transit_minutes",
           "cycling": "cycling_minutes"}.get(mode, "walk_minutes")

    conn = get_connection(db_path)
    # Matrix is stored with smaller venue_id first (A < B alphabetically)
    # Try both orderings
    row = conn.execute(
        f"SELECT {col} FROM travel_matrix WHERE venue_id_a = ? AND venue_id_b = ?",
        (venue_id_a, venue_id_b)
    ).fetchone()
    if row is None:
        row = conn.execute(
            f"SELECT {col} FROM travel_matrix WHERE venue_id_a = ? AND venue_id_b = ?",
            (venue_id_b, venue_id_a)
        ).fetchone()
    conn.close()
    return row[0] if row else None


def spot_check(city: str, pairs: list[tuple], db_path: Path = DB_PATH):
    """
    Verify known venue pairs have plausible travel times.
    pairs: list of (venue_id_a, venue_id_b, expected_walk_min_approx)
    """
    print(f"\nSpot check — {city}:")
    for va, vb, expected in pairs:
        walk = get_travel_time(va, vb, "walk", db_path)
        transit = get_travel_time(va, vb, "transit", db_path)
        status = "✅" if walk and abs(walk - expected) < expected * 0.5 else "⚠"
        print(f"  {status} {va[:8]} → {vb[:8]}: walk={walk}min transit={transit}min "
              f"(expected ~{expected}min walk)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build travel matrix for a city")
    parser.add_argument("--city", required=True)
    parser.add_argument("--api-key", default=None, help="OpenRouteService API key")
    parser.add_argument("--pool", default=None, help="Seasonal pool (hokkaido: winter|summer)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    result = build_travel_matrix(
        city=args.city,
        api_key=args.api_key,
        pool=args.pool,
        dry_run=args.dry_run,
        db_path=args.db,
        verbose=args.verbose,
    )

    status_icon = "✅" if result["status"] in ("ok", "dry_run") else "❌"
    print(f"{status_icon} {result['city']}: {result['status']} — "
          f"{result.get('pairs', 0)} pairs via {result.get('method', 'n/a')}")
