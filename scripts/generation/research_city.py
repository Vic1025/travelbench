"""
scripts/generation/research_city.py

RESEARCH_CITY: generates a complete city config from just a city name.
Writes the result to city_config table in the DB.

Two-phase approach:
  Phase 1 — Open data (no LLM, no API key required):
    - Nominatim:  centre_lat, centre_lng, display_name, country
    - Overpass:   districts (OSM admin-level subareas)
    - radius_km:  derived from the Overpass bounding box
    - Open-Meteo: weather_notes (historical climate summary)
    - Calendar:   task_dates (next Saturday 8-12 weeks out)

  Phase 2 — Focused LLM call (2 fields only):
    - local_cuisine_label  (dominant local cuisine)
    - cuisine_variety      (10-12 cuisine types)

Usage:
  python scripts/generation/research_city.py --city london --api-key KEY
  python scripts/generation/research_city.py --city tokyo --api-key KEY --dry-run
"""

import json
import math
import re
import time
import argparse
import sys
from datetime import date, timedelta, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, init_db, DB_PATH, get_city_db_path

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 PROMPT — cuisine only
# ─────────────────────────────────────────────────────────────────────────────

RESEARCH_PROMPT = """You are generating part of a city configuration for TravelBench.

City: {display_name}, {country}
Districts already identified: {districts_list}

Return ONLY a valid JSON object with exactly these 2 fields:

{{
  "local_cuisine_label": "dominant_cuisine",
  "cuisine_variety": ["cuisine1", "cuisine2", ...]
}}

- local_cuisine_label: single lowercase word (e.g. "british", "japanese", "french")
- cuisine_variety: exactly 10-12 cuisine types present in {display_name}'s food scene

Return ONLY the JSON object."""


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OVERPASS_URL  = "https://overpass-api.de/api/interpreter"
_WEATHER_URL   = "https://climate-api.open-meteo.com/v1/climate"
_HEADERS       = {"User-Agent": "TravelBench-CityResearch/1.0"}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_city_geodata(city_name: str) -> dict | None:
    """
    Fetch coordinates, districts, and city boundary polygon from OSM
    (Nominatim + Overpass).
    Returns dict or None on failure.
    """
    if not HAS_REQUESTS:
        print("  ⚠ requests not available — falling back to LLM for geodata")
        return None

    # Step 1: Nominatim — now also pulls polygon_geojson for the city boundary.
    # The polygon is used downstream at venue COMMIT time for point-in-polygon
    # checks (P6-T21 — replaces the leaky 15-km bbox approach that pulled in
    # NJ municipalities for NYC).
    try:
        resp = _requests.get(
            _NOMINATIM_URL,
            params={"q": city_name, "format": "json", "limit": 1,
                    "addressdetails": 1, "featuretype": "city",
                    "polygon_geojson": 1},
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        print(f"  ⚠ Nominatim failed ({e}) — falling back to LLM")
        return None

    if not results:
        print(f"  ⚠ Nominatim: no result for '{city_name}' — falling back to LLM")
        return None

    r       = results[0]
    lat     = float(r["lat"])
    lng     = float(r["lon"])
    osm_id  = r.get("osm_id")
    osm_type = r.get("osm_type", "relation")
    addr    = r.get("address", {})
    boundary_geojson = r.get("geojson")
    display_name = (addr.get("city") or addr.get("town") or addr.get("county")
                    or city_name.title())
    country = addr.get("country", "")
    print(f"  [geodata] Nominatim: {display_name}, {country} ({lat:.4f}, {lng:.4f})  "
          f"osm_id={osm_id}  polygon_type={(boundary_geojson or {}).get('type')}")

    # Step 2: Overpass — children of the city's OSM relation (not the leaky
    # 15-km bbox at hardcoded admin_level=8).
    time.sleep(1.1)  # Nominatim rate limit
    if osm_type == "relation" and osm_id:
        districts = _fetch_district_children_overpass(int(osm_id))
    else:
        print(f"  ⚠ Nominatim returned non-relation type ({osm_type}) — "
              f"district fetch will fall back to LLM")
        districts = []

    # radius_km: pick a sane default based on whether we have a polygon. The
    # polygon is the authoritative boundary now; radius_km is just a hint for
    # the LLM's venue selection prompt (e.g. "venues should be within X km of
    # the centre"). Derive from the polygon's bbox if available.
    radius_km = _radius_from_polygon(boundary_geojson, lat, lng) if boundary_geojson else 8.0

    return {
        "centre_lat": lat, "centre_lng": lng,
        "display_name": display_name, "country": country,
        "districts": districts, "radius_km": radius_km,
        "boundary_geojson": boundary_geojson,
    }


def _fetch_district_children_overpass(city_osm_id: int) -> list[str]:
    """Query Overpass for admin entities that are CHILDREN of the city's OSM
    relation (via `map_to_area` + admin_level filter).

    This replaces the legacy bbox + hardcoded `admin_level=8` query, which
    silently misses NYC's boroughs (level 5/7) and grabs cross-border NJ
    municipalities (level 8) — see P6-T21 audit and `docs/PHASE7_FUTURE_WORK.md`
    P7-F.

    Tries admin_levels 6, 7, 8 in order — the first level that returns
    between 4 and 60 named entities wins. Picks the most granular usable
    level (admin_level=8 for London's 33 boroughs, =7 for NYC's 5+ boroughs,
    =8 for Paris's 20 arrondissements).
    """
    # admin_level varies by country, but the relationship "child of city C"
    # is universal. Some heuristics:
    # - London (UK):   children at admin_level=8 → 33 boroughs ✓
    # - Paris (FR):    children at admin_level=8 → 20 arrondissements ✓
    # - NYC (US):      children at admin_level=7 → 5 boroughs ✓ (and =6
    #                   would also work — 5 counties — but level=7 has the
    #                   familiar names: Manhattan, Brooklyn, ...)
    # - Tokyo (JP):    children at admin_level=7 → 23 special wards ✓
    # We try multiple levels and pick the most granular one that gives a
    # reasonable count.
    candidate_levels = [8, 7, 6, 9]   # try 8 first (matches London/Paris),
                                       # fall through to 7/6/9 for other cities.

    _STRIP_PREFIXES = [
        "london borough of ", "borough of ", "city of ",
        "municipal borough of ", "royal borough of ",
        "arrondissement de ", "arrondissement du ",
    ]

    best: list[str] = []
    best_level: int | None = None
    for level in candidate_levels:
        query = f"""
[out:json][timeout:25];
relation({city_osm_id});
map_to_area;
relation["admin_level"="{level}"]["name"](area);
out tags;
"""
        try:
            resp = _requests.post(_OVERPASS_URL, data={"data": query},
                                  headers=_HEADERS, timeout=30)
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
        except Exception as e:
            print(f"  ⚠ Overpass admin_level={level} failed ({e})")
            time.sleep(1.1)
            continue

        seen, names = set(), []
        for el in elements:
            tags = el.get("tags", {})
            name = (tags.get("name:en") or tags.get("name", "")).strip()
            if not name or name.isdigit() or len(name) <= 1:
                continue
            name_lower = name.lower()
            for prefix in _STRIP_PREFIXES:
                if name_lower.startswith(prefix):
                    name = name[len(prefix):].strip().title()
                    break
            key = name.lower().replace("-", " ").replace("'", "")
            if key in seen:
                continue
            seen.add(key)
            names.append(name)

        # We want 4-60 entries. Below 4 = unhelpful; above 60 = too granular
        # (e.g. block-level subdivisions).
        if 4 <= len(names) <= 60:
            best = sorted(names)
            best_level = level
            print(f"  [geodata] Overpass children @ admin_level={level}: "
                  f"{len(names)} districts ({', '.join(best[:5])}...)")
            return best
        else:
            print(f"  [geodata] Overpass children @ admin_level={level}: "
                  f"{len(names)} (out of range 4-60, trying next)")
        time.sleep(1.1)

    print(f"  ⚠ Overpass: no admin_level gave 4-60 children — LLM will supplement")
    return best


def _radius_from_polygon(geojson: dict, centre_lat: float,
                         centre_lng: float) -> float:
    """Compute a sane radius_km hint from the city polygon's bbox extent.
    This is just a guidance number for prompt text — the polygon itself is
    the authoritative boundary for venue acceptance."""
    if not geojson:
        return 8.0
    coords = geojson.get("coordinates") or []
    all_pts = []
    if geojson.get("type") == "Polygon":
        for ring in coords:
            all_pts.extend(ring)
    elif geojson.get("type") == "MultiPolygon":
        for part in coords:
            for ring in part:
                all_pts.extend(ring)
    if not all_pts:
        return 8.0
    # Max distance from centre to any polygon vertex
    cos_lat = math.cos(math.radians(centre_lat))
    max_km = 0.0
    for pt in all_pts:
        try:
            lng, lat = pt[0], pt[1]
        except (TypeError, IndexError):
            continue
        dx = (lng - centre_lng) * 111.0 * cos_lat
        dy = (lat - centre_lat) * 111.0
        d = math.sqrt(dx * dx + dy * dy)
        if d > max_km:
            max_km = d
    # Round to nearest km, clamp to sensible range
    return max(3.0, min(25.0, round(max_km, 1)))


def _compute_task_dates() -> list[str]:
    """6 dates starting on the next Saturday 8+ weeks out."""
    today   = date.today()
    target  = today + timedelta(weeks=8)
    offset  = (5 - target.weekday()) % 7
    start   = target + timedelta(days=offset)
    return [(start + timedelta(days=i)).isoformat() for i in range(6)]


def _fetch_weather_notes(lat: float, lng: float, task_dates: list[str]) -> str:
    """Fetch climate summary from Open-Meteo. Returns plain-English string."""
    if not HAS_REQUESTS or not task_dates or lat == 0.0:
        return "Weather data unavailable — check local forecasts for travel dates."
    start, end = task_dates[0], task_dates[-1]
    try:
        resp = _requests.get(
            _WEATHER_URL,
            params={
                "latitude": lat, "longitude": lng,
                "start_date": start, "end_date": end,
                "models": "EC_Earth3P_HR",
                "daily": "temperature_2m_mean,precipitation_sum",
                "temperature_unit": "celsius",
                "precipitation_unit": "mm",
                "timezone": "auto",
            },
            headers=_HEADERS, timeout=10,
        )
        resp.raise_for_status()
        data  = resp.json()
    except Exception as e:
        print(f"  ⚠ Open-Meteo failed ({e})")
        return f"Weather data unavailable for {start[:7]}."

    daily  = data.get("daily", {})
    temps  = daily.get("temperature_2m_mean", [])
    precip = daily.get("precipitation_sum",   [])

    if not temps:
        return f"Typical weather for this region around {start[:7]}."

    avg_t      = round(sum(t for t in temps if t is not None) / max(len(temps), 1), 1)
    rainy_days = sum(1 for p in precip if p is not None and p > 2.0)
    total_mm   = sum(p for p in precip if p is not None)
    month      = datetime.strptime(start, "%Y-%m-%d").strftime("%B")

    if rainy_days == 0:
        rain_desc = "dry"
    elif rainy_days <= 2:
        rain_desc = "mostly dry with occasional light rain"
    elif rainy_days <= 4:
        rain_desc = "mixed — some rainy days expected"
    else:
        rain_desc = "frequent rain — bring an umbrella"

    notes = f"{month}: avg {avg_t}°C, {rain_desc}. ({rainy_days}/{len(temps)} days with rain, {total_mm:.0f}mm total)"
    print(f"  [weather] {notes}")
    return notes


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — LLM for cuisine
# ─────────────────────────────────────────────────────────────────────────────

def _call_llm_cuisine(display_name: str, country: str, districts: list[str],
                      api_key: str, model: str) -> dict:
    """Call LLM for local_cuisine_label + cuisine_variety only."""
    if not HAS_OPENAI:
        raise ImportError("openai package required")

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    prompt = RESEARCH_PROMPT.format(
        display_name=display_name, country=country,
        districts_list=", ".join(districts[:10]) if districts else "various neighbourhoods",
    )

    def _once(max_tok):
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tok, stream=False,
            messages=[
                {"role": "system", "content": "Return only valid complete JSON."},
                {"role": "user",   "content": prompt},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
        return raw.strip()

    for max_tok in [400, 600]:
        try:
            raw    = _once(max_tok)
            result = json.loads(raw)
            if "local_cuisine_label" in result and "cuisine_variety" in result:
                return result
        except Exception as e:
            print(f"  ⚠ Cuisine LLM attempt failed ({e})")

    print("  ⚠ LLM cuisine call failed — using generic fallback")
    return {
        "local_cuisine_label": "local",
        "cuisine_variety": ["local", "italian", "french", "chinese", "japanese",
                            "indian", "american", "mediterranean", "thai", "mexican"],
    }


def _call_llm_tag_vocabulary(display_name: str, country: str,
                               api_key: str, model: str) -> list[str]:
    """
    P6-T1: generate the city-specific tag-vocabulary extension.

    The universal core + cuisines (defined in handbook.py) already cover most
    venue concepts (vibe, audience, practical, characteristic, meal slot,
    cuisine). This call asks the LLM for ~5-15 additional tag concepts that
    are uniquely meaningful for THIS city — e.g. Tokyo: izakaya, kaiseki,
    onsen; Rio: samba-spot, bloco-route, botequim.

    Returns a JSON list of hyphenated-kebab-case strings, or [] on failure.
    Most cities should return a small list (most concepts are universal).
    """
    if not HAS_OPENAI:
        return []

    # Import lazily to avoid circular dependency between handbook + research_city.
    from scripts.generation.handbook import UNIVERSAL_CORE_TAGS, UNIVERSAL_CUISINES
    universal = sorted(UNIVERSAL_CORE_TAGS | UNIVERSAL_CUISINES)

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    prompt = (
        f"City: {display_name}, {country}\n\n"
        f"Our benchmark uses a canonical tag vocabulary covering vibe, audience, "
        f"practical features, accessibility, dietary options, quality signals, "
        f"venue characteristics, meal slots, and cuisine. The current universal "
        f"vocabulary is:\n"
        f"{', '.join(universal)}\n\n"
        f"Suggest ~5-15 ADDITIONAL tag concepts that are uniquely meaningful for "
        f"{display_name} and NOT already covered above. These should be:\n"
        f"  - Single-axis concepts (don't compound: NOT 'history-royal-heritage')\n"
        f"  - Hyphenated kebab-case strings\n"
        f"  - Concrete venue properties (NOT district names, NOT venue names)\n"
        f"  - Genuinely city-specific (NOT generic concepts that any city has)\n\n"
        f"Examples for other cities:\n"
        f"  Tokyo:  izakaya, kaiseki, onsen, ramen-shop\n"
        f"  Rio:    samba-spot, bloco-route, botequim, churrascaria\n"
        f"  Paris:  brasserie, patisserie, cave-a-vin\n\n"
        f"Return ONLY a JSON list of strings, e.g. [\"izakaya\", \"kaiseki\"]. "
        f"Return [] if no city-specific tags are needed."
    )

    def _once(max_tok):
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tok, stream=False,
            messages=[
                {"role": "system", "content": "Return only a valid JSON list of strings."},
                {"role": "user",   "content": prompt},
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
        return raw.strip()

    for max_tok in [300, 500]:
        try:
            raw    = _once(max_tok)
            result = json.loads(raw)
            if isinstance(result, list):
                # Canonicalise + dedupe
                seen = set(); out = []
                for t in result:
                    if not isinstance(t, str):
                        continue
                    canon = t.strip().lower().replace("_", "-").replace(" ", "-")
                    if canon and canon not in seen:
                        seen.add(canon); out.append(canon)
                return out
        except Exception as e:
            print(f"  ⚠ tag_vocabulary LLM attempt failed ({e})")

    print("  ⚠ tag_vocabulary LLM failed — using empty extension")
    return []


def _call_llm_full_fallback(display_name: str, country: str,
                             api_key: str, model: str) -> dict:
    """Full LLM fallback when Overpass returns < 6 districts."""
    if not HAS_OPENAI:
        return {}
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    prompt = (
        f"Generate a city config for: {display_name}, {country}\n\n"
        f"Return ONLY valid JSON with: districts (10-14 neighbourhoods covering the "
        f"FULL geographic spread — tourist areas, landmark zones, food/arts areas, "
        f"residential — not just the food/nightlife circuit), weather_notes (brief "
        f"June climate), local_cuisine_label, cuisine_variety (10-12 types)."
    )
    for max_tok in [1200, 1600]:
        try:
            resp = client.chat.completions.create(
                model=model, max_tokens=max_tok, stream=False,
                messages=[
                    {"role": "system", "content": "Return only valid complete JSON."},
                    {"role": "user",   "content": prompt},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
            raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
            result = json.loads(raw)
            if result.get("districts"):
                return result
        except Exception as e:
            print(f"  ⚠ LLM full fallback failed ({e})")
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# STUB DATA
# ─────────────────────────────────────────────────────────────────────────────

STUB_CONFIGS = {
    "london": {
        "city": "london", "display_name": "London", "country": "United Kingdom",
        "centre_lat": 51.5074, "centre_lng": -0.1278, "radius_km": 6.0,
        "local_cuisine_label": "british",
        "districts": [
            "Westminster", "City of London", "South Bank", "Kensington",
            "Soho", "Covent Garden", "Shoreditch", "Mayfair",
            "Notting Hill", "Camden", "Bermondsey", "Chelsea",
            "Fitzrovia", "Hackney",
        ],
        "cuisine_variety": [
            "british", "indian", "japanese", "italian", "french",
            "middle-eastern", "chinese", "thai", "spanish", "american",
            "korean", "vietnamese",
        ],
        "task_dates": ["2026-06-06","2026-06-07","2026-06-08",
                       "2026-06-09","2026-06-10","2026-06-11"],
        "weather_notes": "June in London: avg 17°C, mixed — some rainy days expected.",
    },
    "tokyo": {
        "city": "tokyo", "display_name": "Tokyo", "country": "Japan",
        "centre_lat": 35.6762, "centre_lng": 139.6503, "radius_km": 8.0,
        "local_cuisine_label": "japanese",
        "districts": [
            "Shinjuku", "Shibuya", "Asakusa", "Harajuku", "Akihabara",
            "Ginza", "Roppongi", "Ueno", "Shimokitazawa", "Nakameguro",
            "Marunouchi", "Odaiba",
        ],
        "cuisine_variety": [
            "japanese", "ramen", "sushi", "izakaya", "french",
            "italian", "chinese", "korean", "vietnamese", "yakiniku",
            "thai", "american",
        ],
        "task_dates": ["2026-05-02","2026-05-03","2026-05-04",
                       "2026-05-05","2026-05-06","2026-05-07"],
        "weather_notes": "May in Tokyo: avg 20°C, mostly dry with occasional light rain.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def research_city(city_name: str, api_key: str = None,
                  model: str = "claude-sonnet-4-20250514",
                  db_path: Path = None,
                  dry_run: bool = False) -> dict:
    """Generate and store a city config. Returns the config dict."""
    city_key = city_name.lower().replace(" ", "_")

    # Check if already exists
    conn = get_connection(db_path)
    existing = conn.execute("SELECT * FROM city_config WHERE city = ?",
                             (city_key,)).fetchone()
    if existing:
        conn.close()
        print(f"  ℹ City '{city_key}' already in DB — skipping generation.")
        cfg = dict(existing)
        for col in ("districts", "cuisine_variety", "task_dates", "tag_vocabulary"):
            if isinstance(cfg.get(col), str):
                try: cfg[col] = json.loads(cfg[col])
                except Exception: pass
        return cfg
    conn.close()

    if dry_run or not api_key:
        if city_key in STUB_CONFIGS:
            print(f"  Using stub config for '{city_key}'")
            config = dict(STUB_CONFIGS[city_key])
        else:
            print(f"  No stub for '{city_key}' — using minimal placeholder")
            config = _minimal_placeholder(city_key, city_name)
        _save_config(config, db_path)
        _print_summary(config)
        return config

    # Phase 1: open data
    print(f"  [Phase 1] Fetching geodata for '{city_name}'...")
    geodata = _fetch_city_geodata(city_name)

    # P6-T23: task_dates and weather_notes are now vestigial — task generation
    # uses seasonal_windows (with per-window weather_notes added in
    # populate_seasonal_windows.py). We write empty defaults to satisfy the
    # legacy NOT NULL constraint without computing values nobody reads.
    if geodata and len(geodata.get("districts", [])) >= 6:
        phase1 = {
            "city": city_key,
            "display_name":  geodata["display_name"],
            "country":       geodata["country"],
            "centre_lat":    geodata["centre_lat"],
            "centre_lng":    geodata["centre_lng"],
            "radius_km":     geodata["radius_km"],
            "districts":     geodata["districts"],
            "task_dates":    [],     # vestigial — see seasonal_windows
            "weather_notes": "",     # vestigial — see seasonal_windows[i].weather_notes
            "boundary_geojson": geodata.get("boundary_geojson"),
        }
        print(f"  [Phase 1] ✓ {len(phase1['districts'])} districts")
    else:
        phase1 = {
            "city": city_key,
            "display_name":  geodata["display_name"] if geodata else city_name.title(),
            "country":       geodata["country"]      if geodata else "",
            "centre_lat":    geodata["centre_lat"]   if geodata else 0.0,
            "centre_lng":    geodata["centre_lng"]   if geodata else 0.0,
            "radius_km":     5.0,
            "districts":     geodata.get("districts", []) if geodata else [],
            "task_dates":    [],     # vestigial — see seasonal_windows
            "weather_notes": "",     # vestigial — see seasonal_windows[i].weather_notes
            "boundary_geojson": geodata.get("boundary_geojson") if geodata else None,
        }

    # Phase 2: LLM for cuisine (and districts if Phase 1 insufficient)
    print(f"  [Phase 2] Calling LLM for cuisine fields...")
    if len(phase1["districts"]) < 6:
        print("  [Phase 2] Overpass insufficient — requesting districts from LLM too...")
        full = _call_llm_full_fallback(phase1["display_name"], phase1["country"],
                                        api_key, model)
        if full.get("districts"):
            phase1["districts"] = full["districts"]
        # P6-T23: weather_notes vestigial; ignore LLM-provided value.
        cuisine = {
            "local_cuisine_label": full.get("local_cuisine_label", "local"),
            "cuisine_variety":     full.get("cuisine_variety", ["local"]),
        }
    else:
        cuisine = _call_llm_cuisine(phase1["display_name"], phase1["country"],
                                     phase1["districts"], api_key, model)

    print(f"  [Phase 2] ✓ cuisine_label='{cuisine['local_cuisine_label']}', "
          f"{len(cuisine['cuisine_variety'])} cuisines")

    # Phase 2b: city-specific tag-vocabulary extension (P6-T1)
    print(f"  [Phase 2b] Calling LLM for city-specific tag-vocabulary extension...")
    tag_vocabulary = _call_llm_tag_vocabulary(
        phase1["display_name"], phase1["country"], api_key, model)
    print(f"  [Phase 2b] ✓ {len(tag_vocabulary)} city-specific tag(s)"
          + (f": {', '.join(tag_vocabulary)}" if tag_vocabulary else ""))

    config = {**phase1, **cuisine, "tag_vocabulary": tag_vocabulary}
    _save_config(config, db_path)
    _print_summary(config)
    return config


def _minimal_placeholder(city_key: str, city_name: str) -> dict:
    return {
        "city": city_key, "display_name": city_name.title(), "country": "Unknown",
        "centre_lat": 0.0, "centre_lng": 0.0, "radius_km": 5.0,
        "local_cuisine_label": "local",
        "districts": ["District 1", "District 2", "District 3"],
        "cuisine_variety": ["local", "italian", "french"],
        "task_dates":    [],   # P6-T23: vestigial
        "weather_notes": "",   # P6-T23: vestigial — see seasonal_windows[i]
    }


def _print_summary(config: dict) -> None:
    print(f"  ✓ City config: {config['display_name']}, {config['country']}")
    d = config.get("districts", [])
    print(f"    Districts ({len(d)}): {', '.join(d[:6])}{'…' if len(d) > 6 else ''}")
    print(f"    Cuisine: {config['local_cuisine_label']} "
          f"({len(config['cuisine_variety'])} types)")
    dates = config.get("task_dates", [])
    if dates:
        print(f"    Task dates: {dates[0]} – {dates[-1]}")


def _compute_bbox(lat: float, lng: float, radius_km: float) -> tuple:
    lat_delta = radius_km / 111.0
    lng_delta = (radius_km / (111.0 * math.cos(math.radians(lat)))
                 if lat != 0.0 else radius_km / 111.0)
    return (round(lat - lat_delta, 6), round(lng - lng_delta, 6),
            round(lat + lat_delta, 6), round(lng + lng_delta, 6))


def _save_config(config: dict, db_path: Path) -> None:
    bbox = _compute_bbox(config.get("centre_lat", 0.0),
                         config.get("centre_lng", 0.0),
                         config.get("radius_km", 5.0))
    bgj = config.get("boundary_geojson")
    boundary_str = json.dumps(bgj) if bgj else ""
    conn = get_connection(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO city_config
            (city, display_name, country, centre_lat, centre_lng,
             radius_km, local_cuisine_label, task_dates,
             districts, cuisine_variety, weather_notes,
             bbox_min_lat, bbox_min_lng, bbox_max_lat, bbox_max_lng,
             tag_vocabulary, boundary_geojson)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        config["city"],
        config.get("display_name", config["city"].title()),
        config.get("country", ""),
        config.get("centre_lat", 0.0),
        config.get("centre_lng", 0.0),
        config.get("radius_km", 5.0),
        config.get("local_cuisine_label", "local"),
        json.dumps(config.get("task_dates", [])),
        json.dumps(config.get("districts", [])),
        json.dumps(config.get("cuisine_variety", [])),
        config.get("weather_notes", ""),
        bbox[0], bbox[1], bbox[2], bbox[3],
        json.dumps(config.get("tag_vocabulary", [])),
        boundary_str,
    ))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--city",    required=True)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--model",   default="claude-sonnet-4-20250514")
    parser.add_argument("--db",      type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    _db = args.db if hasattr(args, "db") and args.db else get_city_db_path(args.city)
    init_db(_db)
    research_city(city_name=args.city, api_key=args.api_key,
                  model=args.model, db_path=_db, dry_run=args.dry_run)
