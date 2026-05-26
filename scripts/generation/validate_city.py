"""
scripts/generation/validate_city.py

Structural audit for a generated city dataset.
Runs after per-venue generation (partial mode) or after full pipeline (full mode).

Partial mode checks: venue pool structure only
  - All venues have required fields (no nulls on required columns)
  - District spread (no district > 40% of venues)
  - Pace distribution (roughly 25-35% each tier)
  - Food/sight pool counts (target 20 each)
  - Wrong-info count (at least 3, not more than 30% of venues)
  - All has_official_site=1 venues have official_site_docs rows

Full mode adds:
  - Every venue has at least one source doc
  - Every wrong_info entry has a truth_carrier doc registered
  - No truth_carrier doc predates its corresponding incorrect_source doc
  - Tags table populated for all venues
  - Wrong info field diversity (>60% same affected_field → warn)
  - Wrong info category diversity (>80% temporal_decay → warn)
  - Likes ordering variance (truth_carrier likes should be ≥ incorrect_source at least once)
  - Source doc type diversity per venue (≥2 doc_types when venue has ≥2 docs)
  - Incorrect value variety (identical closing-time delta in >3 entries → warn)
  - Doc body length (< 150 chars → warn)

Usage:
  python scripts/generation/validate_city.py --city london
  python scripts/generation/validate_city.py --city london --full
  python scripts/generation/validate_city.py --city london --fix-report
"""

import sys
import json
import argparse
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path

FOOD_CATEGORIES = {"restaurant", "cafe", "bar"}
SIGHT_CATEGORIES = {"museum", "attraction", "park", "neighbourhood"}
PACE_TIERS = {"relaxed", "moderate", "intense"}

# Targets
TARGET_FOOD   = 20   # for full 50-venue pool; scaled at runtime
TARGET_SIGHTS = 20   # same
TARGET_TOTAL  = TARGET_FOOD + TARGET_SIGHTS
MAX_DISTRICT_FRACTION = 0.40
WRONG_INFO_MIN = 3              # absolute floor; scaled at runtime
WRONG_INFO_MIN_FRACTION = 0.06  # ~3/50 — scales with pool size
WRONG_INFO_MAX_FRACTION = 0.30
PACE_MIN_FRACTION = 0.20  # each tier should be at least 20% of venues


def validate_city(city: str, full: bool = False,
                  db_path: Path = None,
                  task_dir: Path = None) -> dict:
    """
    Run structural audit for a city. Returns dict with passed bool and issues list.
    """
    conn = get_connection(db_path)
    issues = []
    warnings = []

    # ── Check city exists ─────────────────────────────────────────────────────
    city_row = conn.execute("SELECT * FROM city_config WHERE city = ?", (city,)).fetchone()
    if city_row is None:
        conn.close()
        return {"passed": False, "issues": [f"City '{city}' not found in city_config table."],
                "warnings": [], "stats": {}}

    venues = conn.execute("SELECT * FROM venues WHERE city = ?", (city,)).fetchall()
    n = len(venues)

    # Scale thresholds with actual pool size
    _target_food   = max(3, round(n * 0.40))   # ~40% food/drink
    _target_sights = max(2, round(n * 0.40))   # ~40% sights
    _wi_min        = max(1, round(n * WRONG_INFO_MIN_FRACTION))  # ~6% wrong info

    stats = {
        "city": city,
        "total_venues": n,
        "food_venues": 0,
        "sight_venues": 0,
        "districts": {},
        "pace_distribution": {},
        "wrong_info_count": 0,
        "venues_missing_fields": [],
        "venues_missing_source_docs": [],
        "unregistered_truth_carriers": [],
    }

    if n == 0:
        conn.close()
        return {"passed": False, "issues": ["No venues found for this city."],
                "warnings": [], "stats": stats}

    # ── Required field check ──────────────────────────────────────────────────
    required_cols = [
        "name", "category", "district", "lat", "lng", "avg_cost_local",
        "price_tier", "recommended_visit_minutes", "outdoor_sensitivity",
        "recommended_pace", "traffic_tier", "total_results",
        "yelp_popularity_score", "noise_level", "food_available"
    ]
    for v in venues:
        vd = dict(v)
        missing = [c for c in required_cols if vd.get(c) is None or vd.get(c) == ""]
        if missing:
            stats["venues_missing_fields"].append({
                "venue_id": vd["venue_id"],
                "name": vd.get("name", "?"),
                "missing": missing
            })

    if stats["venues_missing_fields"]:
        issues.append(
            f"{len(stats['venues_missing_fields'])} venue(s) have missing required fields: "
            f"{[v['venue_id'] for v in stats['venues_missing_fields']]}"
        )

    # ── Category counts ───────────────────────────────────────────────────────
    food_venues = [v for v in venues if dict(v)["category"] in FOOD_CATEGORIES]
    sight_venues = [v for v in venues if dict(v)["category"] in SIGHT_CATEGORIES]
    stats["food_venues"] = len(food_venues)
    stats["sight_venues"] = len(sight_venues)

    if len(food_venues) < _target_food:
        warnings.append(
            f"Food pool: {len(food_venues)} venues (target {_target_food} for {n}-venue pool). "
            "May be insufficient for multi-day tasks."
        )
    if len(sight_venues) < _target_sights:
        warnings.append(
            f"Sight pool: {len(sight_venues)} venues (target {_target_sights} for {n}-venue pool). "
            "May be insufficient for multi-day tasks."
        )

    # ── District spread ───────────────────────────────────────────────────────
    district_counts = Counter(dict(v)["district"] for v in venues if dict(v)["district"])
    stats["districts"] = dict(district_counts)
    for district, count in district_counts.items():
        fraction = count / n
        if fraction > MAX_DISTRICT_FRACTION:
            issues.append(
                f"District '{district}' has {count}/{n} venues ({fraction:.0%}) — "
                f"exceeds {MAX_DISTRICT_FRACTION:.0%} max"
            )

    # ── District coverage: check config districts that got 0 venues ──────────
    try:
        import json as _json
        cfg_row = conn.execute(
            "SELECT districts FROM city_config WHERE city = ?", (city,)
        ).fetchone()
        if cfg_row and cfg_row["districts"]:
            cfg_districts = _json.loads(cfg_row["districts"])
            assigned = set(d.lower().strip() for d in district_counts)
            for d in cfg_districts:
                if d.lower().strip() not in assigned:
                    warnings.append(
                        f"District '{d}' (from city config) has 0 venues assigned — "
                        f"the planning LLM may have skipped it. "
                        f"Consider regenerating or adjusting the district list."
                    )
    except Exception:
        pass  # non-critical check

    # ── Pace distribution ─────────────────────────────────────────────────────
    VALID_PACE = {"relaxed", "moderate", "intense"}
    invalid_pace = [dict(v)["venue_id"] for v in venues
                    if dict(v)["recommended_pace"] and
                    dict(v)["recommended_pace"] not in VALID_PACE]
    if invalid_pace:
        issues.append(
            f"{len(invalid_pace)} venue(s) have invalid recommended_pace values "
            f"(must be relaxed|moderate|intense): {invalid_pace}"
        )
    pace_counts = Counter(dict(v)["recommended_pace"] for v in venues
                          if dict(v)["recommended_pace"] in VALID_PACE)
    stats["pace_distribution"] = dict(pace_counts)
    for tier in PACE_TIERS:
        fraction = pace_counts.get(tier, 0) / n
        if fraction < PACE_MIN_FRACTION:
            warnings.append(
                f"Pace tier '{tier}': {pace_counts.get(tier, 0)}/{n} venues ({fraction:.0%}) — "
                f"below {PACE_MIN_FRACTION:.0%} minimum"
            )

    # ── Wrong info count ─────────────────────────────────────────────────────
    wi_count = conn.execute(
        "SELECT COUNT(*) FROM wrong_info WHERE venue_id IN "
        "(SELECT venue_id FROM venues WHERE city = ?)", (city,)
    ).fetchone()[0]
    stats["wrong_info_count"] = wi_count
    if wi_count < _wi_min:
        issues.append(
            f"Only {wi_count} wrong_info entries (minimum {_wi_min} for {n}-venue pool). "
            "Benchmark needs information traps."
        )
    if n > 0 and wi_count / n > WRONG_INFO_MAX_FRACTION:
        warnings.append(
            f"{wi_count}/{n} venues ({wi_count/n:.0%}) have wrong info — "
            f"above {WRONG_INFO_MAX_FRACTION:.0%} recommended max"
        )

    # ── Official site doc coverage ────────────────────────────────────────────
    official_needed = conn.execute(
        "SELECT venue_id FROM venues WHERE city = ? AND has_official_site = 1", (city,)
    ).fetchall()
    for row in official_needed:
        vid = row["venue_id"]
        has_doc = conn.execute(
            "SELECT 1 FROM official_site_docs WHERE venue_id = ?", (vid,)
        ).fetchone()
        if not has_doc:
            issues.append(f"Venue {vid} has_official_site=1 but no official_site_docs row")

    # ── Coordinate spread check (A6.5) ───────────────────────────────────────
    # High-traffic venues should not all cluster within ~1km of each other
    high_venues = [dict(v) for v in venues if dict(v)["traffic_tier"] == "high"
                   and dict(v).get("lat") and dict(v).get("lng")]
    if len(high_venues) >= 3:
        lats = [v["lat"] for v in high_venues]
        lngs = [v["lng"] for v in high_venues]
        lat_spread = max(lats) - min(lats)
        lng_spread = max(lngs) - min(lngs)
        # ~0.009 degrees ≈ 1km at mid-latitudes
        if lat_spread < 0.009 and lng_spread < 0.009:
            warnings.append(
                f"High-traffic venues appear heavily clustered (lat spread={lat_spread:.4f}°, "
                f"lng spread={lng_spread:.4f}°). Type 5 tasks may be unsatisfiable across districts."
            )

    # ── Tag diversity check (A6.5) ────────────────────────────────────────────
    # Key tags needed for Type 5 intersection tasks — should have ≥2 venues each.
    # Tag normalisation: LLM writes variants like "free entry" vs "free-entry";
    # we match all normalised forms by stripping hyphens, underscores, and spaces.
    # P6-T20: vocab updated after T2-B cleanup — removed regulation-mirror tags
    # (dog-friendly, wheelchair-accessible, family-friendly, outdoor) since those
    # concepts now live in dedicated columns (regulations, outdoor_sensitivity).
    KEY_TAGS = ["halal", "free-entry", "live-music", "vegetarian-options",
                "vegan-options", "step-free", "hidden-gem", "instagrammable",
                "cocktails"]

    # Build a normalised lookup: canonical_key -> list of matching raw tags in DB.
    # Strip hyphens, underscores, AND spaces — the venue agent uses all three formats
    # inconsistently ("wheelchair-accessible" vs "wheelchair_accessible" vs "wheelchair accessible").
    def _norm(s: str) -> str:
        return s.lower().replace("-", "").replace("_", "").replace(" ", "")

    # Fetch all tags for this city once
    all_city_tags = conn.execute(
        "SELECT DISTINCT t.tag FROM tags t "
        "JOIN venues v ON t.venue_id = v.venue_id WHERE v.city = ?", (city,)
    ).fetchall()
    all_city_tag_strings = [r[0] for r in all_city_tags]

    # For each canonical key tag, find all DB tags that normalise to the same string
    tag_counts = {}
    for canonical in KEY_TAGS:
        norm_key = _norm(canonical)
        # Find all raw tags in DB that normalise to the same form
        matching_raw = [t for t in all_city_tag_strings if _norm(t) == norm_key]
        if matching_raw:
            count = conn.execute(
                "SELECT COUNT(*) FROM tags t JOIN venues v ON t.venue_id = v.venue_id "
                "WHERE v.city = ? AND t.tag IN ({})".format(
                    ",".join("?" * len(matching_raw))
                ),
                (city, *matching_raw)
            ).fetchone()[0]
        else:
            count = 0
        tag_counts[canonical] = count

    sparse_tags = [t for t, c in tag_counts.items() if c < 2]
    if sparse_tags:
        warnings.append(
            f"Low tag coverage (< 2 venues) for: {sparse_tags}. "
            f"Type 5 constraint intersection tasks may be unsatisfiable."
        )
    stats["tag_diversity"] = tag_counts

    # ── FULL MODE CHECKS ──────────────────────────────────────────────────────
    if full:
        # Every venue has at least one source doc
        for v in venues:
            vid = dict(v)["venue_id"]
            doc_count = conn.execute(
                "SELECT COUNT(*) FROM doc_venue_refs WHERE venue_id = ?", (vid,)
            ).fetchone()[0]
            if doc_count == 0:
                stats["venues_missing_source_docs"].append(vid)
        if stats["venues_missing_source_docs"]:
            issues.append(
                f"{len(stats['venues_missing_source_docs'])} venue(s) have no source docs: "
                f"{stats['venues_missing_source_docs']}"
            )

        # Every wrong_info entry has a truth_carrier
        wi_rows = conn.execute(
            "SELECT wi.wrong_info_id, wi.venue_id FROM wrong_info wi "
            "JOIN venues v ON wi.venue_id = v.venue_id WHERE v.city = ?", (city,)
        ).fetchall()
        for wi in wi_rows:
            carrier = conn.execute(
                "SELECT 1 FROM doc_venue_roles WHERE wrong_info_id = ? AND role = 'truth_carrier'",
                (wi["wrong_info_id"],)
            ).fetchone()
            if carrier is None:
                stats["unregistered_truth_carriers"].append(wi["wrong_info_id"])
        if stats["unregistered_truth_carriers"]:
            issues.append(
                f"{len(stats['unregistered_truth_carriers'])} wrong_info entries "
                f"have no truth_carrier: {stats['unregistered_truth_carriers']}"
            )

        # Date ordering: incorrect_source docs must predate truth_carrier docs
        # for the same wrong_info_id
        wi_ids = conn.execute(
            "SELECT DISTINCT wrong_info_id FROM doc_venue_roles WHERE wrong_info_id IS NOT NULL"
        ).fetchall()
        for row in wi_ids:
            wid = row["wrong_info_id"]
            incorrect_dates = [r["date"] for r in conn.execute(
                "SELECT s.date FROM source_docs s "
                "JOIN doc_venue_roles r ON s.doc_id = r.doc_id "
                "WHERE r.wrong_info_id = ? AND r.role = 'incorrect_source'", (wid,)
            ).fetchall()]
            carrier_dates = [r["date"] for r in conn.execute(
                "SELECT s.date FROM source_docs s "
                "JOIN doc_venue_roles r ON s.doc_id = r.doc_id "
                "WHERE r.wrong_info_id = ? AND r.role = 'truth_carrier'", (wid,)
            ).fetchall()]
            if incorrect_dates and carrier_dates:
                latest_incorrect = max(incorrect_dates)
                earliest_carrier = min(carrier_dates)
                if latest_incorrect >= earliest_carrier:
                    issues.append(
                        f"wrong_info {wid}: incorrect_source date ({latest_incorrect}) >= "
                        f"truth_carrier date ({earliest_carrier}) — ordering is wrong"
                    )

        # Tags populated
        venues_without_tags = []
        for v in venues:
            vid = dict(v)["venue_id"]
            tag_count = conn.execute(
                "SELECT COUNT(*) FROM tags WHERE venue_id = ?", (vid,)
            ).fetchone()[0]
            if tag_count == 0:
                venues_without_tags.append(vid)
        if venues_without_tags:
            issues.append(
                f"{len(venues_without_tags)} venue(s) have no tags: {venues_without_tags}"
            )

        # ── Wrong info field diversity ─────────────────────────────────────────
        wi_all = conn.execute(
            "SELECT wi.affected_field, wi.wrong_info_category FROM wrong_info wi "
            "JOIN venues v ON wi.venue_id = v.venue_id WHERE v.city = ?", (city,)
        ).fetchall()
        if wi_all:
            field_counts = Counter(row["affected_field"] for row in wi_all)
            top_field, top_count = field_counts.most_common(1)[0]
            if top_count / len(wi_all) > 0.60:
                warnings.append(
                    f"Wrong info field diversity: '{top_field}' appears in "
                    f"{top_count}/{len(wi_all)} entries ({top_count/len(wi_all):.0%}) — "
                    f"spread across more fields"
                )

            # ── Wrong info category diversity ──────────────────────────────────
            cat_counts = Counter(row["wrong_info_category"] for row in wi_all)
            td_count = cat_counts.get("temporal_decay", 0)
            if td_count / len(wi_all) > 0.80:
                warnings.append(
                    f"Wrong info category diversity: {td_count}/{len(wi_all)} entries "
                    f"({td_count/len(wi_all):.0%}) are temporal_decay — "
                    f"add conditional or subjective entries"
                )

        # ── Likes ordering variance ────────────────────────────────────────────
        wi_city_ids = conn.execute(
            "SELECT wi.wrong_info_id FROM wrong_info wi "
            "JOIN venues v ON wi.venue_id = v.venue_id WHERE v.city = ?", (city,)
        ).fetchall()
        carrier_gte_count = 0
        comparable_pairs = 0
        for wi_row in wi_city_ids:
            wid = wi_row["wrong_info_id"]
            incorrect_likes = conn.execute(
                "SELECT MAX(s.likes) FROM source_docs s "
                "JOIN doc_venue_roles r ON s.doc_id = r.doc_id "
                "WHERE r.wrong_info_id = ? AND r.role = 'incorrect_source'", (wid,)
            ).fetchone()[0]
            carrier_likes = conn.execute(
                "SELECT MAX(s.likes) FROM source_docs s "
                "JOIN doc_venue_roles r ON s.doc_id = r.doc_id "
                "WHERE r.wrong_info_id = ? AND r.role = 'truth_carrier'", (wid,)
            ).fetchone()[0]
            if incorrect_likes is not None and carrier_likes is not None:
                comparable_pairs += 1
                if carrier_likes >= incorrect_likes:
                    carrier_gte_count += 1
        if comparable_pairs > 0 and carrier_gte_count == 0:
            warnings.append(
                f"Likes ordering: truth_carrier likes are always lower than incorrect_source "
                f"across all {comparable_pairs} wrong_info entries — "
                f"vary credibility so corrections sometimes look more authoritative"
            )

        # ── Source doc type diversity per venue ───────────────────────────────
        single_type_venues = []
        for v in venues:
            vid = dict(v)["venue_id"]
            doc_count = conn.execute(
                "SELECT COUNT(*) FROM doc_venue_refs WHERE venue_id = ?", (vid,)
            ).fetchone()[0]
            if doc_count >= 2:
                type_rows = conn.execute(
                    "SELECT DISTINCT s.doc_type FROM source_docs s "
                    "JOIN doc_venue_refs d ON s.doc_id = d.doc_id "
                    "WHERE d.venue_id = ?", (vid,)
                ).fetchall()
                if len(type_rows) < 2:
                    single_type_venues.append(vid)
        if single_type_venues:
            warnings.append(
                f"{len(single_type_venues)} venue(s) with 2+ source docs use only one "
                f"doc_type (need both blog and forum): {single_type_venues}"
            )

        # ── Incorrect value variety (closing time delta) ───────────────────────
        import re as _re

        def _parse_close_min(s):
            if not s:
                return None
            m = _re.search(r'-(\d{2}):(\d{2})$', s.strip())
            if not m:
                return None
            return int(m.group(1)) * 60 + int(m.group(2))

        hours_wi_rows = conn.execute(
            "SELECT wi.incorrect_value, wi.correct_value FROM wrong_info wi "
            "JOIN venues v ON wi.venue_id = v.venue_id "
            "WHERE v.city = ? AND wi.affected_field LIKE 'hours_%'", (city,)
        ).fetchall()
        close_deltas = []
        for row in hours_wi_rows:
            inc_close = _parse_close_min(row["incorrect_value"])
            cor_close = _parse_close_min(row["correct_value"])
            if inc_close is not None and cor_close is not None:
                close_deltas.append(inc_close - cor_close)
        if close_deltas:
            delta_counts = Counter(close_deltas)
            top_delta, top_delta_count = delta_counts.most_common(1)[0]
            if top_delta_count > 3:
                direction = "later" if top_delta > 0 else "earlier"
                warnings.append(
                    f"Incorrect value variety: closing time delta of {abs(top_delta)} min "
                    f"{direction} appears in {top_delta_count} hours wrong_info entries — "
                    f"vary the offsets"
                )

        # ── Doc body length ────────────────────────────────────────────────────
        short_docs = conn.execute(
            "SELECT s.doc_id, LENGTH(s.body) AS body_len "
            "FROM source_docs s "
            "JOIN doc_venue_refs d ON s.doc_id = d.doc_id "
            "JOIN venues v ON d.venue_id = v.venue_id "
            "WHERE v.city = ? AND LENGTH(s.body) < 150 "
            "GROUP BY s.doc_id", (city,)
        ).fetchall()
        if short_docs:
            warnings.append(
                f"{len(short_docs)} source doc(s) have body < 150 chars: "
                f"{[(r['doc_id'], r['body_len']) for r in short_docs]}"
            )

        # ── B7: avg_venue_difficulty populated ────────────────────────────────
        unscored = conn.execute(
            "SELECT COUNT(*) FROM venues "
            "WHERE city = ? AND venue_difficulty_score IS NULL "
            "AND page_status = 'verified'", (city,)
        ).fetchone()[0]
        if unscored > 0:
            warnings.append(
                f"{unscored} verified venue(s) have no venue_difficulty_score. "
                f"Run compute_venue_difficulty.py --city {city}"
            )
        stats["unscored_venues"] = unscored

        # ── B7: task set checks (only if task_dir exists) ─────────────────────
        import json as _json
        from pathlib import Path as _Path
        from scripts.generation.populate_seasonal_windows import get_windows

        # Find task files for this city
        if task_dir is not None:
            _search_dirs = [task_dir]
        else:
            _search_dirs = [
                _Path(__file__).parent.parent.parent / "data" / "data1" / "tasks",
                _Path(__file__).parent.parent.parent / "data" / "tasks",
            ]
        task_files = []
        for td in _search_dirs:
            if td.exists():
                task_files.extend(td.glob(f"{city[:3]}_*.json"))
                task_files.extend(td.glob(f"{city}_*.json"))

        if task_files:
            tasks = []
            for tf in task_files:
                try:
                    t = _json.loads(tf.read_text())
                    if t.get("city") == city:
                        tasks.append(t)
                except Exception:
                    pass

            stats["task_count"] = len(tasks)

            if tasks:
                # Structural type coverage
                from scripts.generation.compute_task_difficulty import STRUCTURAL_TYPE_WEIGHT
                types_present = {t.get("structural_type") for t in tasks
                                 if t.get("structural_type")}
                types_missing = set(STRUCTURAL_TYPE_WEIGHT.keys()) - types_present
                if types_missing:
                    warnings.append(
                        f"Structural types not represented in task set: {types_missing}"
                    )
                stats["structural_types_covered"] = sorted(types_present)

                # Seasonal window coverage
                windows = get_windows(city, db_path=db_path)
                if windows:
                    window_task_counts = {}
                    for w in windows:
                        wid = w["window_id"]
                        count = sum(1 for t in tasks if t.get("window_id") == wid)
                        window_task_counts[wid] = count
                        if count < 3:
                            warnings.append(
                                f"Window '{wid}' has only {count} task(s) "
                                f"(recommend ≥ 3 per window)"
                            )
                    stats["window_task_counts"] = window_task_counts

                # avg_venue_difficulty populated on tasks
                tasks_missing_avd = [
                    t.get("task_id") for t in tasks
                    if t.get("avg_venue_difficulty") is None
                ]
                if tasks_missing_avd:
                    warnings.append(
                        f"{len(tasks_missing_avd)} task(s) missing avg_venue_difficulty. "
                        f"Run compute_task_difficulty.py"
                    )
        else:
            stats["task_count"] = 0

    conn.close()

    passed = len(issues) == 0
    return {
        "passed": passed,
        "mode": "full" if full else "partial",
        "issues": issues,
        "warnings": warnings,
        "stats": stats
    }


def print_report(result: dict) -> None:
    """Print a human-readable audit report."""
    stats = result["stats"]
    city = stats.get("city", "unknown")
    n = stats.get("total_venues", 0)

    print(f"\n{'='*60}")
    print(f"VALIDATE CITY: {city.upper()} ({result['mode']} mode)")
    print(f"{'='*60}")
    print(f"Total venues: {n}")
    print(f"  Food/drink: {stats.get('food_venues', 0)}")
    print(f"  Sights:     {stats.get('sight_venues', 0)}")
    print(f"Wrong info entries: {stats.get('wrong_info_count', 0)}")

    if stats.get("pace_distribution"):
        print(f"Pace distribution: {stats['pace_distribution']}")
    if stats.get("districts"):
        print(f"Districts ({len(stats['districts'])}): {list(stats['districts'].keys())}")

    if result["warnings"]:
        print(f"\n⚠  Warnings ({len(result['warnings'])}):")
        for w in result["warnings"]:
            print(f"   • {w}")

    if result["issues"]:
        print(f"\n❌ Issues ({len(result['issues'])}):")
        for issue in result["issues"]:
            print(f"   • {issue}")
    else:
        print(f"\n✅ All checks passed")

    print(f"\nResult: {'PASSED' if result['passed'] else 'FAILED'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=None,
                        help="DB path override (default: data/cities/{city}/travelbench.db)")
    parser.add_argument("--city", required=True)
    parser.add_argument("--full", action="store_true",
                        help="Run full checks (post multi-venue doc generation)")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = validate_city(args.city, full=args.full)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result)
    sys.exit(0 if result["passed"] else 1)
