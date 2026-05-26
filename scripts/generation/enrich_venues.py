"""
scripts/generation/enrich_venues.py

ENRICH_VENUES: blocking step between PLAN_VENUES and GENERATE_VENUE.

For each high/mid traffic venue brief, queries OpenStreetMap via the
Overpass API to find matching real-world nodes and extract:
  - lat, lng (coordinates)
  - address (from addr:* tags)
  - relevant policy tags (wheelchair only — maps to DB regulation)

Opening hours are deliberately NOT extracted — the data generation agent
invents these from scratch (detecting hallucination is a feature).

Low traffic venues skip Overpass entirely.
Venues not found get overpass_match=False — generation proceeds without reference.

Usage:
  from scripts.generation.enrich_venues import enrich_briefs
  enriched = enrich_briefs(briefs, city_config)
"""

import json
import time
import math
import http.client
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# OVERPASS QUERY
# ─────────────────────────────────────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM amenity/tourism types that map to our categories
OSM_TYPE_FILTERS = {
    "restaurant": '["amenity"~"restaurant|food_court"]',
    "cafe":       '["amenity"~"cafe|coffee_shop"]',
    "bar":        '["amenity"~"bar|pub|biergarten"]',
    "museum":     '["tourism"="museum"]',
    "attraction": '["tourism"~"attraction|artwork|viewpoint|theme_park"]',
    "park":       '["leisure"~"park|garden|nature_reserve"]',
    "neighbourhood": None,  # neighbourhoods aren't OSM nodes — skip
}

# Policy tags we extract — only wheelchair maps to a DB regulation field.
# Other OSM tags (smoking, outdoor_seating, fee, internet_access) were
# extracted previously but had zero downstream consumers. Removed.
POLICY_TAG_MAP = {
    "wheelchair":       "wheelchair",          # yes/no/limited
}


def _overpass_query(name: str, category: str,
                    bbox: tuple, radius_expand: float = 1.0) -> list[dict]:
    """
    Query Overpass for all venues of the given category within the bounding box.
    Returns list of candidate dicts with lat, lng, tags.
    bbox: (min_lat, min_lng, max_lat, max_lng)

    Queries both `name` and `name:en` fields to handle non-Latin cities
    (Istanbul, Shanghai, etc.) where OSM stores local-script names but
    the venue brief uses English transliteration.
    """
    if category == "neighbourhood":
        return []

    osm_filter = OSM_TYPE_FILTERS.get(category)
    if not osm_filter:
        # Fallback: search any named node
        osm_filter = '["name"]'

    min_lat, min_lng, max_lat, max_lng = bbox
    bbox_str = f"{min_lat},{min_lng},{max_lat},{max_lng}"

    # Query nodes, ways, and relations — cast to centre point for ways/relations
    # Also query name:en for non-Latin cities (Istanbul, Shanghai, etc.)
    query = f"""
[out:json][timeout:30];
(
  node{osm_filter}["name"]({bbox_str});
  way{osm_filter}["name"]({bbox_str});
  relation{osm_filter}["name"]({bbox_str});
  node{osm_filter}["name:en"]({bbox_str});
  way{osm_filter}["name:en"]({bbox_str});
  relation{osm_filter}["name:en"]({bbox_str});
);
out center tags;
"""
    encoded = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(
        OVERPASS_URL,
        data=encoded,
        headers={"User-Agent": "TravelBench-DataGen/1.0 (research project)"}
    )
    transient_errors = (
        urllib.error.URLError,
        http.client.IncompleteRead,
        http.client.RemoteDisconnected,
        ConnectionError,
        TimeoutError,
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            return data.get("elements", [])
        except json.JSONDecodeError:
            return []
        except transient_errors:
            if attempt == 2:
                return []
            time.sleep(2 ** attempt)
    return []


def _extract_coords(element: dict) -> tuple[float, float]:
    """Extract lat/lng from node or way/relation (which have a 'center' key)."""
    if element.get("type") == "node":
        return element["lat"], element["lon"]
    center = element.get("center", {})
    return center.get("lat", 0.0), center.get("lon", 0.0)


def _extract_address(tags: dict) -> str | None:
    """Build address string from OSM addr:* tags."""
    parts = []
    for key in ["addr:housenumber", "addr:street", "addr:city", "addr:postcode"]:
        val = tags.get(key)
        if val:
            parts.append(val)
    return ", ".join(parts) if parts else None


def _extract_policies(tags: dict) -> dict:
    """Extract relevant policy boolean/enum tags."""
    policies = {}
    for our_key, osm_key in POLICY_TAG_MAP.items():
        val = tags.get(osm_key)
        if val:
            policies[our_key] = val
    return policies


# ─────────────────────────────────────────────────────────────────────────────
# NAME SIMILARITY
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    """
    Normalize a venue name for fuzzy comparison:
      - Handle Turkish special characters (ı→i, İ→I, ğ→g, ş→s)
      - Strip diacritics (Çırağan → Ciragan, Güllüoğlu → Gulluoglu)
      - Lowercase
      - Remove punctuation
    This significantly improves matching for Turkish, Arabic, and CJK venue names
    where OSM `name:en` may use slightly different transliterations.
    """
    import unicodedata
    # Turkish special replacements before NFKD (NFKD doesn't handle these)
    s = s.replace("ı", "i").replace("İ", "I").replace("ğ", "g").replace("Ğ", "G")
    s = s.replace("ş", "s").replace("Ş", "S")
    # NFKD decomposition: separates base chars from diacritics
    nfkd = unicodedata.normalize("NFKD", s)
    # Keep only ASCII printable (drops combining diacritical marks)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    # Lowercase and strip punctuation
    return ascii_only.lower().strip()


def _name_similarity(a: str, b: str) -> float:
    """
    Token-overlap similarity between two venue name strings.
    Returns 0.0–1.0. Case-insensitive, diacritic-normalized, ignores stop words.

    Normalization handles non-Latin cities: "Çırağan Palace" matches
    "Ciragan Palace Kempinski" after stripping diacritics.
    Also compares against `name:en` if present in OSM element.
    """
    STOP = {"the", "a", "an", "of", "and", "&", "at", "in", "le", "la",
            "les", "de", "du", "di", "el", "los", "das", "der", "die",
            "restaurant", "cafe", "bar", "hotel", "lokantasi", "lokantası"}

    def tokens(s: str) -> set:
        normalized = _normalize(s)
        return {w.strip(".,'-") for w in normalized.split()
                if w.strip(".,'-") and w.lower() not in STOP and len(w) > 1}

    a_tok = tokens(a)
    b_tok = tokens(b)
    if not a_tok or not b_tok:
        return 0.0
    intersection = a_tok & b_tok
    union = a_tok | b_tok
    return len(intersection) / len(union)


def _find_best_match(name: str, candidates: list[dict],
                     min_similarity: float = 0.35) -> dict | None:
    """
    Find the best-matching candidate from Overpass results by name similarity.
    Returns the candidate dict if similarity >= min_similarity, else None.

    Checks both `name` and `name:en` OSM tags. This is essential for
    non-Latin cities where:
      - `name` stores local script (Turkish, Arabic, Chinese)
      - `name:en` stores English transliteration matching the brief
    Also uses diacritic-normalized comparison via _name_similarity().
    """
    best_score = 0.0
    best = None
    for elem in candidates:
        tags = elem.get("tags", {})
        # Check both name and name:en — critical for non-Latin cities
        candidates_names = [
            tags.get("name", ""),
            tags.get("name:en", ""),
            tags.get("name:tr", ""),   # Turkish
            tags.get("name:ar", ""),   # Arabic
            tags.get("name:zh", ""),   # Chinese
        ]
        for osm_name in candidates_names:
            if not osm_name:
                continue
            score = _name_similarity(name, osm_name)
            if score > best_score:
                best_score = score
                best = elem
    if best_score >= min_similarity:
        return best
    return None


# ─────────────────────────────────────────────────────────────────────────────
# NOMINATIM FALLBACK
# ─────────────────────────────────────────────────────────────────────────────

def _nominatim_lookup(name: str, city: str, category: str) -> dict | None:
    """
    Fallback geocoder using Nominatim (OSM search API).
    Handles transliteration gaps and diacritic mismatches better than raw Overpass,
    because Nominatim does its own fuzzy matching on the server side.

    Returns an element dict compatible with _extract_coords() and _extract_policies(),
    or None if not found or rate-limited.

    Rate limit: Nominatim requires max 1 request/second.
    """
    import unicodedata
    # Normalize query — Nominatim handles some diacritics but ASCII is safer
    nfkd   = unicodedata.normalize("NFKD", name)
    q_name = nfkd.encode("ascii", "ignore").decode("ascii").strip()

    query = f"{q_name}, {city}"
    params = urllib.parse.urlencode({
        "q":              query,
        "format":         "jsonv2",
        "limit":          3,
        "addressdetails": 1,
        "extratags":      1,
    })
    url = f"https://nominatim.openstreetmap.org/search?{params}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "TravelBench-DataGen/1.0 (research project)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read().decode())
        time.sleep(1.0)  # Nominatim rate limit: 1 req/sec
    except Exception:
        return None

    if not results:
        return None

    # Pick the highest-ranked result and wrap it in our element format
    r = results[0]
    extra = r.get("extratags") or {}
    addr  = r.get("address") or {}
    return {
        "type": "node",
        "lat":  float(r.get("lat", 0)),
        "lon":  float(r.get("lon", 0)),
        "tags": {
            "name":            r.get("display_name", name).split(",")[0].strip(),
            "name:en":         r.get("display_name", name).split(",")[0].strip(),
            "wheelchair":      extra.get("wheelchair"),
            "addr:street":     addr.get("road"),
            "addr:city":       addr.get("city") or addr.get("town"),
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENRICHMENT
# ─────────────────────────────────────────────────────────────────────────────

def enrich_briefs(briefs: list[dict], city_config: dict,
                  rate_limit_seconds: float = 1.5,
                  dry_run: bool = False) -> list[dict]:
    """
    Enrich venue briefs with Overpass data for high/mid traffic venues.
    Low traffic venues pass through unchanged.

    Returns enriched briefs — each has:
      overpass_match: True | False | None (None = not attempted / low traffic)
      overpass_ref: {lat, lng, address, policies} | None
    """
    # Get bounding box from city_config
    bbox = (
        city_config.get("bbox_min_lat"),
        city_config.get("bbox_min_lng"),
        city_config.get("bbox_max_lat"),
        city_config.get("bbox_max_lng"),
    )
    has_bbox = all(v is not None for v in bbox)

    if not has_bbox:
        # Compute from centre + radius if not stored
        import math as _math
        lat = city_config["centre_lat"]
        lng = city_config["centre_lng"]
        r = city_config.get("radius_km", 6.0)
        lat_d = r / 111.0
        lng_d = r / (111.0 * _math.cos(_math.radians(lat)))
        bbox = (
            round(lat - lat_d, 6), round(lng - lng_d, 6),
            round(lat + lat_d, 6), round(lng + lng_d, 6),
        )

    enriched = []
    n_high_mid = sum(1 for b in briefs if b["traffic_tier"] in ("high", "mid"))
    done = 0

    for brief in briefs:
        tier = brief["traffic_tier"]

        if tier == "low":
            # Skip Overpass for low traffic
            enriched.append({**brief, "overpass_match": None, "overpass_ref": None})
            continue

        if dry_run:
            # In dry-run: simulate a match for the first high venue, no match for rest
            if tier == "high" and done == 0:
                enriched.append({
                    **brief,
                    "overpass_match": True,
                    "overpass_ref": {
                        "lat": city_config["centre_lat"] + 0.01,
                        "lng": city_config["centre_lng"] + 0.01,
                        "address": f"1 Example Street, {city_config['display_name']}",
                        "policies": {"wheelchair": "yes"}
                    }
                })
            else:
                enriched.append({**brief, "overpass_match": False, "overpass_ref": None})
            done += 1
            continue

        # Real Overpass query
        done += 1
        print(f"  [{done:2d}/{n_high_mid}] Overpass: {brief['name'][:45]}", end=" ... ", flush=True)

        candidates = _overpass_query(brief["name"], brief["category"], bbox)
        match = _find_best_match(brief["name"], candidates)

        if not match:
            # Nominatim fallback — handles transliteration gaps better than raw Overpass
            match = _nominatim_lookup(
                brief["name"],
                city_config.get("display_name", ""),
                brief["category"]
            )
            if match:
                print(f"✓ via Nominatim", end=" ")

        if match:
            lat, lng = _extract_coords(match)
            tags = match.get("tags", {})
            osm_name = tags.get("name:en") or tags.get("name", "?")
            ref = {
                "lat": lat,
                "lng": lng,
                "address": _extract_address(tags),
                "policies": _extract_policies(tags),
                "osm_name": osm_name,
            }
            print(f"✓ matched '{osm_name[:35]}'")
            enriched.append({**brief, "overpass_match": True, "overpass_ref": ref})
        else:
            print(f"✗ not found")
            enriched.append({**brief, "overpass_match": False, "overpass_ref": None})

        # Respectful rate limiting
        time.sleep(rate_limit_seconds)

    matched = sum(1 for b in enriched if b.get("overpass_match") is True)
    not_found = sum(1 for b in enriched if b.get("overpass_match") is False)
    skipped = sum(1 for b in enriched if b.get("overpass_match") is None)
    print(f"\n  Overpass results: {matched} matched, {not_found} not found, {skipped} low-traffic skipped")

    return enriched
