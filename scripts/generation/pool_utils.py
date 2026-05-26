"""
scripts/generation/pool_utils.py

Shared venue pool utilities used by task generation, doc generation,
difficulty scoring, and validation.

Functions extracted from:
  - generate_task.py      : load_venue_pool, load_unavailable_dates, _apply_pool_filters
  - test_generate_tasks.py: load_city_pool, load_pool, get_window_for_city,
                            build_pool_inventory
  - task_agent.py         : _city_centre_from_pool, _query_pool, _get_venue_detail

Each original file keeps a thin import alias so no external callers change.
constraint_engine.py stays as its own module (already stable, different concern).
"""

from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

from scripts.generation.db import get_connection, DB_PATH, get_city_db_path
from scripts.generation.constraint_engine import PRICE_TIER_ORDER


# Cuisine tag values used for count_distinct aggregation over meals.
# A venue's cuisine_label is the first tag that matches this set.
_CUISINE_TYPES: frozenset[str] = frozenset({
    "french","italian","spanish","japanese","chinese","vietnamese",
    "thai","indian","mexican","korean","mediterranean","middle-eastern",
    "american","fusion","asian","tapas","brasserie","moroccan","british",
    "greek","turkish","lebanese","peruvian","argentinian","caribbean",
})


# ─────────────────────────────────────────────────────────────────────────────
# CITY METADATA
# ─────────────────────────────────────────────────────────────────────────────

# city → ISO 3166-1 alpha-2 country code. Babel does the rest
# (country → primary currency, currency → symbol). Adding a new city only
# requires this one entry. (P6-T12)
CITY_COUNTRY: dict[str, str] = {
    "london":        "GB",
    "paris":         "FR",
    "tokyo":         "JP",
    "istanbul":      "TR",
    "new_york":      "US",
    "san_francisco": "US",
    "berlin":        "DE",
    "rome":          "IT",
    "barcelona":     "ES",
    "sydney":        "AU",
    "singapore":     "SG",
    "hong_kong":     "HK",
    "seoul":         "KR",
    "bangkok":       "TH",
    "dubai":         "AE",
    "mumbai":        "IN",
    "mexico_city":   "MX",
    "buenos_aires":  "AR",
    "rio":           "BR",
    "cairo":         "EG",
}

# Lazy-import Babel: if unavailable, fall back to a small built-in table so
# the pipeline still runs on minimal environments. Babel is the standard dep
# for currency / locale lookups; this guard is for skeleton installs only.
try:
    from babel.numbers import get_territory_currencies as _babel_currencies
    from babel.numbers import get_currency_symbol as _babel_symbol
    _HAS_BABEL = True
except ImportError:
    _HAS_BABEL = False
    _FALLBACK_CURRENCY: dict[str, str] = {
        "GB": "GBP", "FR": "EUR", "JP": "JPY", "TR": "TRY", "US": "USD",
        "DE": "EUR", "IT": "EUR", "ES": "EUR", "AU": "AUD", "SG": "SGD",
        "HK": "HKD", "KR": "KRW", "TH": "THB", "AE": "AED", "IN": "INR",
        "MX": "MXN", "AR": "ARS", "BR": "BRL", "EG": "EGP",
    }
    _FALLBACK_SYMBOL: dict[str, str] = {
        "GBP": "£", "EUR": "€", "USD": "$", "JPY": "¥", "TRY": "₺",
        "AUD": "A$", "SGD": "S$", "HKD": "HK$", "KRW": "₩", "THB": "฿",
        "AED": "د.إ", "INR": "₹", "MXN": "Mex$", "ARS": "AR$", "BRL": "R$",
        "EGP": "E£",
    }


def get_city_currency(city: str) -> str:
    """
    Return the ISO 4217 currency code for a city's local currency.
    Uses CITY_COUNTRY → Babel territory currency lookup.
    Defaults to "USD" with a print-warning for unknown cities (safe fallback —
    avg_cost_local values are unitless locally, currency code is only used for
    rendering error messages to users).
    """
    country = CITY_COUNTRY.get(city.lower())
    if not country:
        print(f"[pool_utils] warning: city {city!r} has no country mapping in "
              f"CITY_COUNTRY; defaulting to USD for display purposes.")
        return "USD"
    if _HAS_BABEL:
        currencies = _babel_currencies(country)
        if currencies:
            # Babel returns a set; sort for deterministic output across runs.
            return sorted(currencies)[0]
        return "USD"
    return _FALLBACK_CURRENCY.get(country, "USD")


def format_money(amount: float, city: str) -> str:
    """
    Render an amount in the city's local currency for display in error
    messages and logs. Example: format_money(47.5, "london") → "£48".
    Uses whole-number rounding; fractional costs are rarely meaningful for
    per-person estimates.
    """
    code = get_city_currency(city)
    if _HAS_BABEL:
        sym = _babel_symbol(code, locale="en_US")
    else:
        sym = _FALLBACK_SYMBOL.get(code, code + " ")
    return f"{sym}{amount:.0f}"


# ─────────────────────────────────────────────────────────────────────────────
# DB LOAD FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def load_venue_pool(city: str, db_path: Path = None) -> list[dict]:
    """
    Load venue briefs for the task/doc generation agent.
    Returns only the fields the agent needs — NOT source doc bodies.

    Per design: agent receives name, category, district, traffic_tier,
    character, tags, has_wrong_info (bool), seasonal_windows, event info.
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    conn = get_connection(db_path)

    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(venues)").fetchall()}
    wf_expr      = "v.window_flags" if "window_flags" in existing_cols else "NULL as window_flags"
    lc_expr      = "v.local_cuisine" if "local_cuisine" in existing_cols else "NULL as local_cuisine"
    cuisine_expr = "v.cuisine"       if "cuisine"       in existing_cols else "NULL as cuisine"

    venues = conn.execute(f"""
        SELECT v.venue_id, v.name, v.category, v.district, v.traffic_tier,
               v.recommended_pace, v.price_tier, v.recommended_visit_minutes,
               v.avg_cost_local, v.lat, v.lng,
               v.has_wrong_info_planned, {wf_expr},
               v.pet_friendly, v.wheelchair_accessible, v.age_restriction,
               v.family_friendly, v.noise_level, {lc_expr}, {cuisine_expr},
               v.booking_required, v.has_official_site,
               v.photography_allowed, v.dress_code, v.reservation_required,
               v.outside_food_allowed, v.food_available, v.parking_nearby,
               v.outdoor_sensitivity
        FROM venues v
        WHERE v.city = ? AND v.page_status = 'verified'
        ORDER BY v.traffic_tier DESC, v.name
    """, (city,)).fetchall()

    pool = []
    for row in venues:
        v   = dict(row)
        vid = v["venue_id"]
        tags = conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
        ).fetchall()
        v["tags"] = [t["tag"] for t in tags]
        try:
            v["window_flags"] = json.loads(v.get("window_flags") or "{}")
        except Exception:
            v["window_flags"] = {}
        v["has_wrong_info"] = bool(v.pop("has_wrong_info_planned", 0))
        # W2: surface wrong-info category to task agent so it can craft relevant tasks.
        # W3 (conditional activation dates) deferred — columns not yet in schema.
        if v["has_wrong_info"]:
            wi_rows = conn.execute(
                "SELECT wrong_info_category FROM wrong_info WHERE venue_id = ?",
                (vid,)
            ).fetchall()
            cats = {row["wrong_info_category"] for row in wi_rows
                    if row["wrong_info_category"]}
            v["wrong_info_categories"] = sorted(cats)
            v["wrong_info_activation"]  = []   # populated once W3 columns added to schema
        else:
            v["wrong_info_categories"] = []
            v["wrong_info_activation"]  = []
        pool.append(v)

    conn.close()
    return pool


def load_city_pool(city: str, db_path: Path = None) -> list[dict]:
    """
    Load venue pool from SQLite DB for any city.
    Raises ValueError with clear message if city not found or has no verified venues.
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    conn = get_connection(db_path)

    city_row = conn.execute(
        "SELECT * FROM city_config WHERE city = ?", (city,)
    ).fetchone()
    if city_row is None:
        conn.close()
        raise ValueError(
            f"City '{city}' not found in DB. "
            f"Run: python scripts/generation/generate_city_venues.py --city {city}"
        )

    cols   = {r[1] for r in conn.execute("PRAGMA table_info(venues)").fetchall()}
    wf_col      = "window_flags"  if "window_flags"  in cols else "NULL as window_flags"
    lc_col      = "local_cuisine" if "local_cuisine" in cols else "NULL as local_cuisine"
    cuisine_col = "cuisine"       if "cuisine"       in cols else "NULL as cuisine"

    venues = conn.execute(f"""
        SELECT v.venue_id, v.name, v.category, v.district, v.traffic_tier,
               v.recommended_pace, v.price_tier, v.recommended_visit_minutes,
               v.avg_cost_local, v.lat, v.lng,
               v.has_wrong_info_planned,
               v.booking_required, v.has_official_site,
               v.family_friendly, v.wheelchair_accessible,
               v.photography_allowed, v.pet_friendly,
               v.age_restriction, v.noise_level,
               v.dress_code, v.reservation_required,
               v.outside_food_allowed, v.food_available,
               v.parking_nearby, v.outdoor_sensitivity,
               {wf_col}, {lc_col}, {cuisine_col}
        FROM venues v
        WHERE v.city = ? AND v.page_status = 'verified'
    """, (city,)).fetchall()

    if not venues:
        conn.close()
        raise ValueError(
            f"No verified venues found for '{city}'. "
            f"Run venue generation pipeline first."
        )

    pool = []
    for row in venues:
        v   = dict(row)
        vid = v["venue_id"]
        tags = [r["tag"] for r in conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
        ).fetchall()]
        v["tags"]           = tags
        v["has_wrong_info"] = bool(v.pop("has_wrong_info_planned", 0))
        v["window_flags"]   = json.loads(v.get("window_flags") or "{}")
        # Derive cuisine_label: first tag that is a known cuisine type.
        # Enables count_distinct aggregation over meals without pattern-specific logic.
        v["cuisine_label"] = next(
            (t for t in tags if t in _CUISINE_TYPES), None
        )
        pool.append(v)

    conn.close()
    return pool


def load_pool(city: str, db_path: Path = None) -> tuple[list[dict], dict]:
    """
    Load venue pool + city config for any city from DB.
    Raises ValueError with clear message if city not in DB.
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    pool = load_city_pool(city, db_path=db_path)

    conn    = get_connection(db_path)
    row     = conn.execute(
        "SELECT * FROM city_config WHERE city = ?", (city,)
    ).fetchone()
    conn.close()

    if row is None:
        raise ValueError(f"City config not found for '{city}' in DB.")

    cfg_raw = dict(row)
    for col in ("districts", "cuisine_variety", "task_dates"):
        if isinstance(cfg_raw.get(col), str):
            try:
                cfg_raw[col] = json.loads(cfg_raw[col] or "[]")
            except Exception:
                cfg_raw[col] = []

    cfg = {
        "city":                city,
        "display_name":        cfg_raw.get("display_name", city.title()),
        "country":             cfg_raw.get("country", ""),
        "local_cuisine_label": cfg_raw.get("local_cuisine_label", "local"),
        "districts":           cfg_raw.get("districts", []),
        "centre_lat":          cfg_raw.get("centre_lat"),
        "centre_lng":          cfg_raw.get("centre_lng"),
    }
    return pool, cfg


def load_unavailable_dates(city: str, window_dates: list[str],
                            db_path: Path = None) -> dict[str, list[str]]:
    """
    Return {venue_id: [date_str, ...]} for venues with sold-out dates
    within the given window date range.
    """
    if not window_dates:
        return {}
    if db_path is None:
        db_path = get_city_db_path(city)
    conn         = get_connection(db_path)
    placeholders = ",".join("?" * len(window_dates))
    rows         = conn.execute(
        f"SELECT ta.venue_id, ta.date FROM ticket_availability ta "
        f"JOIN venues v ON ta.venue_id = v.venue_id "
        f"WHERE v.city = ? AND ta.date IN ({placeholders}) AND ta.sold_out = 1",
        [city] + window_dates
    ).fetchall()
    conn.close()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["venue_id"], []).append(r["date"])
    return result


# ─────────────────────────────────────────────────────────────────────────────
# WINDOW LOADING
# ─────────────────────────────────────────────────────────────────────────────


def get_window_for_city(city: str, window_id: str | None,
                         db_path: Path = None) -> dict:
    """Get window config for a city from the per-city SQLite DB."""
    if db_path is None:
        db_path = get_city_db_path(city)
    from scripts.generation.populate_seasonal_windows import get_window, get_windows

    if window_id:
        w = get_window(city, window_id, db_path=db_path)
        if w:
            return w

    windows = get_windows(city, db_path=db_path)
    if windows:
        return windows[0]

    raise ValueError(
        f"No seasonal windows found for city '{city}'. "
        f"Run: python scripts/generation/populate_seasonal_windows.py --city {city}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# POOL ANALYTICS
# ─────────────────────────────────────────────────────────────────────────────

def build_pool_inventory(pool: list[dict]) -> str:
    """
    Build a compact pool inventory block for generation prompts.
    Shows what tags, regulations, and categories actually exist —
    prevents LLMs from generating constraints referencing labels
    or venue types that don't exist in the pool.
    """
    cat_counts   = Counter(v["category"] for v in pool)
    all_tags     = [tag for v in pool for tag in v.get("tags", [])]
    tag_counts   = Counter(all_tags)
    usable_tags  = sorted(k for k, n in tag_counts.items() if n >= 2)
    rare_tags    = sorted(k for k, n in tag_counts.items() if n == 1)

    reg_keys   = ["wheelchair_accessible","photography_allowed","pet_friendly",
                  "family_friendly","booking_required"]
    reg_counts = {rk: sum(1 for v in pool if v.get(rk)) for rk in reg_keys}

    price_counts   = Counter(v.get("price_tier",  "?") for v in pool)
    traffic_counts = Counter(v.get("traffic_tier", "?") for v in pool)

    lines = ["POOL INVENTORY (what actually exists — use this to choose constraints):", ""]
    lines.append("Categories:")
    for cat, n in sorted(cat_counts.items()):
        lines.append(f"  {cat}: {n} venue(s)")

    lines.append("")
    lines.append("Tags usable in label_required / label_excluded (≥2 venues):")
    lines.append("  " + ", ".join(usable_tags))
    if rare_tags:
        lines.append("Tags with only 1 venue (risky for label constraints):")
        lines.append("  " + ", ".join(rare_tags[:15]) + ("..." if len(rare_tags) > 15 else ""))

    lines.append("")
    lines.append("Regulations (use regulation_required for these — NOT label_required):")
    for rk, n in reg_counts.items():
        lines.append(f"  {rk}: {n}/{len(pool)} venues")

    lines.append("")
    lines.append("Price tiers:   " + ", ".join(f"{k}:{n}" for k, n in sorted(price_counts.items())))
    lines.append("Traffic tiers: " + ", ".join(f"{k}:{n}" for k, n in sorted(traffic_counts.items())))

    return "\n".join(lines)


def _city_centre_from_pool(pool: list[dict]) -> tuple[float, float]:
    """Derive city centre from mean of high-traffic venue coordinates."""
    hi = [(v["lat"], v["lng"]) for v in pool
          if v.get("traffic_tier") == "high" and v.get("lat") and v.get("lng")]
    if not hi:
        hi = [(v["lat"], v["lng"]) for v in pool if v.get("lat") and v.get("lng")]
    if not hi:
        return (48.8, 2.35)  # fallback: Paris
    return (sum(c[0] for c in hi) / len(hi), sum(c[1] for c in hi) / len(hi))


# ─────────────────────────────────────────────────────────────────────────────
# POOL FILTERING
# ─────────────────────────────────────────────────────────────────────────────

def _query_pool(filters: dict, pool: list[dict],
                include: list[str] | None = None) -> list[dict]:
    """
    Filter pool by ALL given conditions (AND logic).
    Returns [{venue_id, name, ...optional fields}] for matching venues.

    Supported filter keys: category, tag, regulation, district,
    traffic_tier, price_tier, booking_required, cuisine.

    include: optional list of extra field groups to attach to each result.
        - "price"    → adds avg_cost_local, price_tier
        - "duration" → adds recommended_visit_minutes
        - "coords"   → adds lat, lng, district
    Default (None or []) keeps results minimal: {venue_id, name}.

    Defensive against malformed LLM filter arguments — a filter with a
    non-string value for tag/category/etc. produces 0 matches rather
    than raising a TypeError. Malformed include values are silently skipped.
    """
    if not isinstance(filters, dict):
        return []
    include_set = set(include or []) if isinstance(include, list) else set()
    results = []
    for v in pool:
        match = True
        for key, value in filters.items():
            if key == "category":
                if v.get("category") != value:
                    match = False; break
            elif key == "tag":
                tags = v.get("tags", [])
                if not isinstance(tags, list):
                    tags = []
                if not isinstance(value, str):
                    match = False; break
                # Normalise both sides: underscores/spaces → hyphens, lowercase.
                # Defends against legacy pool data where tags may be stored in
                # mixed formats, and against agents querying with underscores.
                def _norm_tag(s): return s.lower().replace("_", "-").replace(" ", "-")
                if _norm_tag(value) not in {_norm_tag(t) for t in tags}:
                    match = False; break
            elif key == "regulation":
                regs    = v.get("regulations", {}) if isinstance(v.get("regulations"), dict) else {}
                reg_val = regs.get(value, v.get(value)) if isinstance(value, str) else None
                if not reg_val:
                    match = False; break
            elif key == "district":
                if v.get("district") != value:
                    match = False; break
            elif key == "traffic_tier":
                if v.get("traffic_tier") != value:
                    match = False; break
            elif key == "price_tier":
                if v.get("price_tier") != value:
                    match = False; break
            elif key == "booking_required":
                if bool(v.get("booking_required", 0)) != bool(value):
                    match = False; break
            elif key == "cuisine":
                # P6-T1b: structured cuisine field (restaurants only).
                if v.get("cuisine") != value:
                    match = False; break
        if match:
            entry = {"venue_id": v["venue_id"], "name": v["name"]}
            if "price" in include_set:
                entry["avg_cost_local"] = v.get("avg_cost_local")
                entry["price_tier"]     = v.get("price_tier")
            if "duration" in include_set:
                entry["recommended_visit_minutes"] = v.get("recommended_visit_minutes")
            if "coords" in include_set:
                entry["lat"]      = v.get("lat")
                entry["lng"]      = v.get("lng")
                entry["district"] = v.get("district")
            results.append(entry)
    return results


def _get_venue_detail(venue_id: str, pool_map: dict,
                       unavailable: dict | None = None) -> dict | None:
    """
    Return full venue detail from pool_map, with regulations nested.
    unavailable: {venue_id: [sold_out_date, ...]} — defaults to empty.
    """
    unavailable = unavailable or {}
    v = pool_map.get(venue_id)
    if v is None:
        return None

    if isinstance(v.get("regulations"), dict):
        regs = v["regulations"]
    else:
        regs = {
            "wheelchair_accessible": bool(v.get("wheelchair_accessible", 0)),
            "pet_friendly":          bool(v.get("pet_friendly", 0)),
            "photography_allowed":   bool(v.get("photography_allowed", 1)),
            "family_friendly":       bool(v.get("family_friendly", 1)),
            "age_restriction":       v.get("age_restriction"),
            "dress_code":            v.get("dress_code"),
            "noise_level":           v.get("noise_level", "moderate"),
            "reservation_required":  bool(v.get("reservation_required", 0)),
            "outside_food_allowed":  bool(v.get("outside_food_allowed", 0)),
        }

    return {
        "venue_id":                  v["venue_id"],
        "name":                      v["name"],
        "category":                  v.get("category"),
        "district":                  v.get("district"),
        "traffic_tier":              v.get("traffic_tier"),
        "recommended_pace":          v.get("recommended_pace"),
        "price_tier":                v.get("price_tier"),
        "avg_cost_local":              v.get("avg_cost_local"),
        "lat":                       v.get("lat"),
        "lng":                       v.get("lng"),
        "recommended_visit_minutes": v.get("recommended_visit_minutes", 75),
        "tags":                      v.get("tags", []),
        "regulations":               regs,
        "booking_required":          bool(v.get("booking_required", 0)),
        "has_official_site":         bool(v.get("has_official_site", 0)),
        "window_flags":              v.get("window_flags", {}),
        "unavailable_dates":         unavailable.get(v["venue_id"], []),
    }


def _apply_pool_filters(venue_pool: list, task: dict) -> list:
    """
    Apply hard F-score + P-score constraint filters to venue pool.
    Returns filtered list of venues satisfying all applicable constraints.

    Used by:
    - _verify_task_solvable (pool size > 25 check, Type 2 geometry)
    - compute_avg_venue_difficulty (mean over filtered pool)
    - pool_size_difficulty scoring
    - doc planning (itinerary guide geographic clustering)

    P-score constraints in generic schema (scope/condition/aggregation) are
    handled via venues_matching.  Bucket C pattern-based constraints that have
    a clear venue-level meaning are handled inline; plan-structural ones (e.g.
    dependency_chain, consecutive_pairs) are skipped — they have no venue-
    level filter equivalent.
    """
    from scripts.generation.constraint_engine import venues_matching

    filtered = list(venue_pool)
    rubric   = task.get("rubric", {})

    # 1. Hard F-score explicit_physical constraints
    for c in rubric.get("hard_constraints", []):
        if c.get("explicit_physical"):
            params  = c.get("params", {})
            reg_key = params.get("regulation_key")
            req_val = params.get("required_value")
            if reg_key and req_val is not None:
                def _reg_val(v, key):
                    regs = v.get("regulations")
                    if isinstance(regs, dict):
                        return regs.get(key)
                    return v.get(key)
                filtered = [v for v in filtered
                            if _reg_val(v, reg_key) == (1 if req_val else 0)]

    if not filtered:
        return filtered

    # 2. P-score venue-level constraints
    for c in rubric.get("personal_constraints", []):

        # ── Generic schema constraint → use venues_matching ──────────────────
        if "scope" in c:
            scope     = c["scope"]
            condition = c.get("condition", {})
            agg       = c.get("aggregation", "all")

            # Skip plan-structural scopes that have no venue-level meaning.
            if scope == "per_day":
                continue
            if isinstance(scope, str) and scope.startswith("time_window="):
                continue
            if isinstance(scope, list) and all(
                s == "per_day" or (isinstance(s, str) and s.startswith("time_window="))
                for s in scope
            ):
                continue

            if agg == "none":
                # Exclusion: remove venues satisfying condition within scope.
                matching_ids = {v["venue_id"] for v in venues_matching(filtered, scope, condition)}
                filtered = [v for v in filtered if v["venue_id"] not in matching_ids]

            elif agg == "all":
                # Only apply as a POOL FILTER when scope is truly universal (scope="all"
                # or scope="activity_type=any"). Scoped constraints like
                # "all meals must be upscale" (scope="activity_type=meal") should NOT
                # eliminate site venues from the pool — they constrain which meal
                # venues are usable, not whether museums are accessible at all.
                scope_is_universal = (
                    scope == "all" or
                    scope == "activity_type=any" or
                    (isinstance(scope, str) and scope.startswith("venue_id=")) or
                    (isinstance(scope, str) and scope.startswith("venue_name~"))
                )
                if scope_is_universal:
                    filtered = venues_matching(filtered, scope, condition)
                # else: scoped "all" — NOT a pool filter, handled by scoped_pool logic

            # {at_least: N} and other counting aggregations are INCLUSION
            # constraints — not pool filters. No pool filtering needed.
            continue

        # ── Bucket C pattern constraints: only ones with venue-level meaning ─
        pattern = c.get("pattern", "")

        # Remaining Bucket C patterns (weather_aware, opening_time_required,
        # dependency_chain, consecutive_pairs) have no useful venue-level
        # filter equivalent — skip them here.
        # local_cuisine_preference, cuisine_diversity_minimum, and
        # temporal_cross_day have been migrated to generic schema and will
        # be handled by the "scope" in c branch above.

    return filtered
