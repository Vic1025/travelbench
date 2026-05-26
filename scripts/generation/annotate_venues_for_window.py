"""
scripts/generation/annotate_venues_for_window.py

Post-generation pass that tags venues with window-specific flags.
Enables Type 6 B-score checks (geographic avoidance, timing relative
to anchor events) without requiring venue regeneration.

Each window defines a set of zone geometries (bounding boxes) and
proximity rules. The script checks each venue's coordinates against
these zones and writes flags to the venues.window_flags JSON column.

Flag schema per venue:
  {
    "london_carnival_2026": {
      "in_closure_zone": true,     -- within Notting Hill road closure area
      "near_route": true           -- within ~500m of carnival parade route
    },
    "london_christmas_2026": {
      "confirmed_holiday_closure": false  -- requires hours_overrides check
    },
    "rio_carnival_2026": {
      "on_bloco_route": false,
      "in_sambodromo_zone": false
    },
    "sapporo_snow_festival_2026": {
      "in_odori_zone": false,      -- within Odori Park festival footprint
      "in_susukino_zone": false
    }
  }

Usage:
  python scripts/generation/annotate_venues_for_window.py --city london
  python scripts/generation/annotate_venues_for_window.py --city all
"""

import json
import argparse
import sys
from pathlib import Path
from math import radians, cos, sqrt

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path


# ─────────────────────────────────────────────────────────────────────────────
# ZONE DEFINITIONS
# Each zone is a bounding box: (min_lat, max_lat, min_lng, max_lng)
# Based on real geographic coordinates for each window's relevant areas.
# ─────────────────────────────────────────────────────────────────────────────

ZONES = {
    # London — Notting Hill Carnival (Aug 20-26 2026)
    # Road closure zone covers W11 and parts of W10 and W2
    "london_carnival_closure": (51.505, 51.520, -0.210, -0.185),
    # Wider neighbourhood affected by sound/crowds
    "london_carnival_wider": (51.500, 51.528, -0.220, -0.175),

    # London — Christmas (Dec 23-29 2026)
    # Oxford Street / Covent Garden post-Christmas crowds
    "london_christmas_shopping": (51.510, 51.518, -0.130, -0.115),

    # Rio — Carnival (Feb 12-18 2026)
    # Sambódromo / Marquês de Sapucaí parade route
    "rio_sambodromo": (-22.913, -22.904, -43.199, -43.188),
    # Lapa / Santa Teresa bloco concentration
    "rio_lapa_blocos": (-22.922, -22.906, -43.185, -43.170),
    # Ipanema / Copacabana blocos
    "rio_ipanema_blocos": (-22.990, -22.968, -43.202, -43.175),

    # Sapporo — Snow Festival (Feb 5-11 2026)
    # Odori Park main festival site (2km snow sculpture corridor)
    "sapporo_odori": (43.058, 43.064, 141.345, 141.365),
    # Susukino ice sculpture site
    "sapporo_susukino": (43.053, 43.059, 141.351, 141.358),
}


def _in_zone(lat: float, lng: float, zone_key: str) -> bool:
    """Check if coordinates fall within a named zone bounding box."""
    min_lat, max_lat, min_lng, max_lng = ZONES[zone_key]
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Approximate distance in km between two coordinates."""
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = (dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * (dlng / 2) ** 2
    return R * 2 * sqrt(a)


def _annotate_london(venue: dict) -> dict:
    """Compute window flags for a London venue."""
    lat, lng = venue.get("lat"), venue.get("lng")
    flags = {}

    if lat and lng:
        flags["london_carnival_2026"] = {
            "in_closure_zone": _in_zone(lat, lng, "london_carnival_closure"),
            "in_wider_affected_area": _in_zone(lat, lng, "london_carnival_wider"),
        }
        # Christmas: flag venues in major shopping crowd zones
        flags["london_christmas_2026"] = {
            "in_shopping_crowd_zone": _in_zone(lat, lng, "london_christmas_shopping"),
        }

    # Check hours_overrides for confirmed Christmas closures
    return flags


def _annotate_rio(venue: dict) -> dict:
    """Compute window flags for a Rio venue."""
    lat, lng = venue.get("lat"), venue.get("lng")
    flags = {}

    if lat and lng:
        flags["rio_carnival_2026"] = {
            "in_sambodromo_zone": _in_zone(lat, lng, "rio_sambodromo"),
            "in_lapa_bloco_zone": _in_zone(lat, lng, "rio_lapa_blocos"),
            "in_ipanema_bloco_zone": _in_zone(lat, lng, "rio_ipanema_blocos"),
            # Any of the above means the venue is in a high-impact carnival area
            "in_carnival_impact_zone": any([
                _in_zone(lat, lng, "rio_sambodromo"),
                _in_zone(lat, lng, "rio_lapa_blocos"),
                _in_zone(lat, lng, "rio_ipanema_blocos"),
            ]),
        }
        flags["rio_festas_juninas_2026"] = {
            # Festas Juninas are neighbourhood-specific (North Zone strongest)
            # Venues north of city centre (Tijuca, Madureira area)
            "in_north_zone": lat < -22.90 and lng > -43.28,
        }

    return flags


def _annotate_hokkaido(venue: dict) -> dict:
    """Compute window flags for a Hokkaido/Sapporo venue."""
    lat, lng = venue.get("lat"), venue.get("lng")
    flags = {}

    if lat and lng:
        flags["sapporo_snow_festival_2026"] = {
            "in_odori_zone": _in_zone(lat, lng, "sapporo_odori"),
            "in_susukino_zone": _in_zone(lat, lng, "sapporo_susukino"),
            "in_festival_footprint": any([
                _in_zone(lat, lng, "sapporo_odori"),
                _in_zone(lat, lng, "sapporo_susukino"),
            ]),
        }
        flags["hokkaido_lavender_2026"] = {
            # Furano/Biei area: lavender farms cluster around 43.3-43.6°N, 142.3-142.6°E
            "in_furano_biei_area": 43.2 <= lat <= 43.7 and 142.1 <= lng <= 142.8,
            # Sapporo urban venues are accessible in both windows
            "is_sapporo_urban": 42.9 <= lat <= 43.2 and 141.2 <= lng <= 141.5,
        }

    return flags


CITY_ANNOTATORS = {
    "london": _annotate_london,
    "rio":    _annotate_rio,
    "hokkaido": _annotate_hokkaido,
}


def annotate_city(city: str, db_path: Path = DB_PATH) -> dict:
    """
    Run window annotation for all venues in a city.
    Returns summary of annotations applied.
    """
    annotator = CITY_ANNOTATORS.get(city)
    if annotator is None:
        return {
            "city": city,
            "status": "skipped",
            "note": f"No window annotations defined for '{city}'. "
                    f"Supported: {list(CITY_ANNOTATORS.keys())}"
        }

    conn = get_connection(db_path)
    venues = conn.execute(
        "SELECT venue_id, name, lat, lng, district, category FROM venues WHERE city = ?",
        (city,)
    ).fetchall()

    if not venues:
        conn.close()
        return {"city": city, "status": "no_venues", "annotated": 0}

    annotated = 0
    flag_counts = {}

    for row in venues:
        venue = dict(row)
        flags = annotator(venue)

        if flags:
            conn.execute(
                "UPDATE venues SET window_flags = ? WHERE venue_id = ?",
                (json.dumps(flags), venue["venue_id"])
            )
            annotated += 1

            # Count flag hits for summary
            for window_id, window_flags in flags.items():
                for flag_name, flag_val in window_flags.items():
                    if flag_val:
                        key = f"{window_id}.{flag_name}"
                        flag_counts[key] = flag_counts.get(key, 0) + 1

    conn.commit()
    conn.close()

    return {
        "city": city,
        "status": "ok",
        "total_venues": len(venues),
        "annotated": annotated,
        "flag_counts": flag_counts,
    }


def get_venue_window_flags(venue_id: str, db_path: Path = DB_PATH) -> dict:
    """Read window flags for a specific venue."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT window_flags FROM venues WHERE venue_id = ?", (venue_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return {}
    try:
        return json.loads(row["window_flags"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Annotate venues with seasonal window flags")
    parser.add_argument("--city", default="all",
                        help="City to annotate, or 'all' for all supported cities")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()

    cities = list(CITY_ANNOTATORS.keys()) if args.city == "all" else [args.city]

    for city in cities:
        result = annotate_city(city, db_path=args.db)
        if result["status"] == "skipped":
            print(f"⚠  {city}: {result['note']}")
        elif result["status"] == "no_venues":
            print(f"⚠  {city}: no venues found in DB")
        else:
            print(f"✅ {city}: {result['annotated']}/{result['total_venues']} venues annotated")
            for flag, count in sorted(result["flag_counts"].items()):
                print(f"   {flag}: {count} venue(s)")


if __name__ == "__main__":
    main()
