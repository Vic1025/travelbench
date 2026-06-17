"""
eval/evaluator.py

3-tier evaluation engine for TravelBench.

Tier 1 - C-Score (Completion):      Did the agent use tools correctly and produce valid output?
Tier 2 - F-Score (Feasibility):     Is the plan operationally valid?
Tier 3 - P-Score (Personalization): Does it satisfy user preferences? (partial, LLM judge)

Usage:
    python eval/evaluator.py --result results/par_easy_001__claude-sonnet.json
    python eval/evaluator.py --results-dir results/ --summary
"""

import json, math, re, sys, argparse
from pathlib import Path
from typing import Optional
from datetime import date, timedelta

try:
    import anthropic as _anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

ROOT     = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data"

_FENCE_RE = re.compile(r"^```[a-z]*\n?|\n?```$", re.MULTILINE)

def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE INDEX  (Sprint 1)
# Answers: "which document(s) confirm that venue V has label L?"
# Built once per city on first use; keyed by (venue_id, label) → [doc_ids]
# ─────────────────────────────────────────────────────────────────────────────

_source_index_cache: dict[str, dict] = {}   # city → index

def build_source_index(city: str) -> dict:
    """
    Returns nested dict: {venue_id: {label_or_reg_key: [doc_ids]}}

    doc_id format:
      - blog/forum: the doc's "doc_id" field (e.g. "par_blog_006")
      - official site: "official:<venue_id>"  (e.g. "official:par_s02")

    Labels are matched by keyword presence in doc content for blogs/forums.
    Official sites confirm all their full_labels + full_regulations keys.
    """
    if city in _source_index_cache:
        return _source_index_cache[city]

    src_dir = DATA_DIR / "sources" / city
    # Load venues from DB (replaces venues.json)
    try:
        venues, _, _ = load_ground_truth(city)
    except (ValueError, Exception):
        venues = {}
    index: dict = {}

    # ── Blogs and forums ──────────────────────────────────────────────────
    bf_dir = src_dir / "blogs_and_forums"
    if bf_dir.exists():
        for fpath in sorted(bf_dir.glob("*.json")):
            doc     = json.loads(fpath.read_text())
            doc_id  = doc.get("doc_id", fpath.stem)
            content = doc.get("content", "").lower()
            for vid in doc.get("venues_mentioned", []):
                if vid not in venues:
                    continue
                for label in venues[vid].get("tags", []):
                    keyword = label.replace("-", " ")
                    if keyword in content or label.lower() in content:
                        index.setdefault(vid, {}).setdefault(label, []).append(doc_id)
                # Also index regulation keywords
                for reg_key, reg_val in venues[vid].get("regulations", {}).items():
                    keyword = reg_key.replace("_", " ")
                    if keyword in content:
                        index.setdefault(vid, {}).setdefault(f"reg:{reg_key}", []).append(doc_id)

    # ── Official sites ────────────────────────────────────────────────────
    off_dir = src_dir / "official_sites"
    if off_dir.exists():
        for fpath in sorted(off_dir.glob("*.json")):
            doc    = json.loads(fpath.read_text())
            vid    = doc.get("venue_id")
            if not vid:
                continue
            oid = f"official:{vid}"
            for label in doc.get("full_labels", []):
                index.setdefault(vid, {}).setdefault(label, []).append(oid)
            for reg_key in doc.get("full_regulations", {}):
                index.setdefault(vid, {}).setdefault(f"reg:{reg_key}", []).append(oid)

    _source_index_cache[city] = index
    return index


def _source_consulted(tool_log: list, venue_id: str, source_type: str) -> bool:
    """
    Returns True if the agent's tool_call_log shows it consulted the
    required source for the given venue_id.

      source_type == "official_site"      → agent called get_official_site(venue_id)
      source_type == "blogs_and_forums"   → agent called search_blogs_and_forums
                                            with a query/venue_name matching venue_id
                                            (or any blog call, since results are relevance-ranked)
    """
    if source_type == "official_site":
        return any(
            call.get("tool_name") == "get_official_site"
            and call.get("tool_input", {}).get("venue_id") == venue_id
            for call in tool_log
        )
    if source_type == "blogs_and_forums":
        for call in tool_log:
            if call.get("tool_name") != "search_blogs_and_forums":
                continue
            inp = call.get("tool_input", {})
            # Accept if: venue_name matches, OR doc_id appears in output results
            if inp.get("venue_name") == venue_id:
                return True
            # Check if a result containing this venue was actually returned
            for r in call.get("tool_output", {}).get("results", []):
                if venue_id in r.get("venues_mentioned", []):
                    return True
        return False
    return False   # unknown source_type — conservative


def _apply_source_required(base_result: dict, constraint: dict,
                            tool_log: list, city: str) -> dict:
    """
    Post-processes a code handler result with source_required logic.

    If the handler already scored 0.0, source_required doesn't help — keep 0.0.
    If the handler scored > 0 AND source_required is set AND agent didn't consult
    the source → halve the credit (lucky guess penalty).
    """
    source_type = constraint.get("source_required")
    if not source_type:
        return base_result                   # no source requirement — pass through

    if base_result["score"] <= 0.0:
        return base_result                   # already failed — source check irrelevant

    # Find the most relevant venue for this constraint
    params      = constraint.get("params", {})
    atype       = params.get("activity_type", "any")
    source_idx  = build_source_index(city)

    # Determine what label/reg to look up
    label   = params.get("required_label") or params.get("excluded_label")
    reg_key = params.get("regulation_key")
    index_key = label or (f"reg:{reg_key}" if reg_key else None)

    if not index_key:
        return base_result                   # can't determine — pass through

    # Find venues in the plan that are relevant to this constraint
    # (we need any one of them to have been source-confirmed)
    confirmed_venues = [vid for vid, v_idx in source_idx.items()
                        if index_key in v_idx]

    if not confirmed_venues:
        return base_result                   # no known confirming source — pass through

    # Check whether agent consulted source for at least one confirming venue
    consulted = any(
        _source_consulted(tool_log, vid, source_type)
        for vid in confirmed_venues
    )

    if consulted:
        return {**base_result, "source_verified": True}

    # Lucky guess: plan was correct but agent never checked the source
    penalised_score = round(base_result["score"] * 0.5, 3)
    return {
        **base_result,
        "score":           penalised_score,
        "source_verified": False,
        "reason":          base_result.get("reason", "") + (
            f"  [source_required={source_type}: not consulted → score ×0.5]"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def hhmm_to_min(t: str) -> int:
    """Parse a time string to minutes-since-midnight.

    Accepts canonical "HH:MM" (24-hour). Also tolerates 12-hour suffixes
    ("9:00 AM", "12:30 pm") by stripping the meridiem and adjusting hours —
    some solver models emit times this way despite prompt instructions.
    """
    s = t.strip()
    meridiem = None
    upper = s.upper()
    if upper.endswith(" AM") or upper.endswith(" PM"):
        meridiem = upper[-2:]
        s = s[:-3].strip()
    elif upper.endswith("AM") or upper.endswith("PM"):
        meridiem = upper[-2:]
        s = s[:-2].strip()
    h, m = map(int, s.split(":"))
    if meridiem == "AM" and h == 12:
        h = 0
    elif meridiem == "PM" and h != 12:
        h += 12
    return h * 60 + m

def min_to_hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"

def load_ground_truth(city: str, db_path=None) -> tuple[dict, dict]:
    """
    Load venues dict and travel matrix dict for a city from SQLite DB.

    Venues dict: {venue_id: venue_dict} with nested structure matching
    the evaluator's expected format (hours, regulations, labels, etc.)

    Matrix dict: {v1_to_v2: {"walking": N, "transit": N}}

    Raises ValueError if city not found in DB.
    Emits warning (does not raise) if travel matrix table is empty.
    """
    import sys as _sys
    _sys.path.insert(0, str(ROOT))
    from scripts.generation.db import get_connection, get_city_db_path
    city = city.lower()
    if db_path is None:
        db_path = get_city_db_path(city)

    conn = get_connection(db_path)

    # Check city exists in DB
    try:
        city_row = conn.execute(
            "SELECT city FROM city_config WHERE city = ?", (city,)
        ).fetchone()
    except Exception:
        city_row = None
        conn.close()

    if city_row is None:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        raise ValueError(
            f"City '{city}' not found in DB. "
            f"Run: python scripts/generation/generate_city_venues.py --city {city}"
        )

    # Load all verified venues
    venue_rows = conn.execute("""
        SELECT venue_id, name, category, district, address,
               lat, lng,
               hours_mon, hours_tue, hours_wed, hours_thu,
               hours_fri, hours_sat, hours_sun,
               avg_cost_local, price_tier, recommended_visit_minutes,
               booking_required, has_official_site,
               outdoor_sensitivity, recommended_pace, traffic_tier,
               pet_friendly, wheelchair_accessible, parking_nearby,
               age_restriction, dress_code, photography_allowed,
               noise_level, reservation_required, outside_food_allowed,
               family_friendly, food_available,
               has_wrong_info_planned, local_cuisine, cuisine,
               window_flags
        FROM venues
        WHERE city = ? AND page_status = 'verified'
    """, (city,)).fetchall()

    venues: dict = {}
    for row in venue_rows:
        v = dict(row)
        vid = v["venue_id"]

        # Hours: flat columns → nested dict
        v["hours"] = {
            "mon": v.pop("hours_mon"),
            "tue": v.pop("hours_tue"),
            "wed": v.pop("hours_wed"),
            "thu": v.pop("hours_thu"),
            "fri": v.pop("hours_fri"),
            "sat": v.pop("hours_sat"),
            "sun": v.pop("hours_sun"),
        }
        # Normalize hours to evaluator format: {day: ["HH:MM-HH:MM", ...] | None}
        # DB stores: "09:30-18:00" (single), "12:00-14:30, 17:30-22:00" (multi),
        #            "Closed" (string), or None (closed).
        # Evaluator expects: ["09:30-18:00"] (list of combined strings) or None.
        _norm_hours = {}
        for _dk, _hval in v["hours"].items():
            if _hval is None:
                continue  # closed — omit from dict
            if isinstance(_hval, str):
                if _hval.lower() == "closed":
                    continue  # closed — omit
                # Split comma-separated windows: "12:00-14:30, 17:30-22:00"
                _norm_hours[_dk] = [w.strip() for w in _hval.split(",") if w.strip()]
            elif isinstance(_hval, list):
                _norm_hours[_dk] = _hval  # already in list format (Paris legacy)
        v["hours"] = _norm_hours

        # Regulations: flat int columns → nested dict
        v["regulations"] = {
            "pet_friendly":          bool(v.pop("pet_friendly", 0)),
            "wheelchair_accessible": bool(v.pop("wheelchair_accessible", 1)),
            "parking_nearby":        bool(v.pop("parking_nearby", 0)),
            "age_restriction":       v.pop("age_restriction"),  # int or None
            "dress_code":            v.pop("dress_code"),
            "photography_allowed":   bool(v.pop("photography_allowed", 1)),
            "noise_level":           v.pop("noise_level", "moderate"),
            "reservation_required":  bool(v.pop("reservation_required", 0)),
            "outside_food_allowed":  bool(v.pop("outside_food_allowed", 0)),
            "family_friendly":       bool(v.pop("family_friendly", 1)),
        }

        # Tags → tags list (evaluator reads venue["tags"])
        tag_rows = conn.execute(
            "SELECT tag FROM tags WHERE venue_id = ?", (vid,)
        ).fetchall()
        v["tags"] = [r["tag"] for r in tag_rows]

        # Location: flat column → nested dict (evaluator reads venue["location"]["district"])
        v["location"] = {"district": v.get("district", "")}

        # Wrong info: map to both field names the evaluator checks
        v["has_wrong_info"]      = bool(v.pop("has_wrong_info_planned", 0))
        v["has_stale_hours"]     = v["has_wrong_info"]

        # Ticket availability: attach from ticket_availability table
        ticket_rows = conn.execute(
            "SELECT date, sold_out, slots_available, price_local "
            "FROM ticket_availability WHERE venue_id = ?",
            (vid,)
        ).fetchall()
        v["ticket_availability"] = {
            r["date"]: {
                "sold_out":        bool(r["sold_out"]),
                "slots_available": r["slots_available"],
                "price_local":       r["price_local"],
            }
            for r in ticket_rows
        }

        # Parse window_flags JSON
        try:
            import json as _json
            v["window_flags"] = _json.loads(v.get("window_flags") or "{}")
        except Exception:
            v["window_flags"] = {}

        venues[vid] = v

    # Load travel matrix (include cycling + taxi approximation)
    matrix_rows = conn.execute(
        "SELECT venue_id_a, venue_id_b, walk_minutes, transit_minutes, "
        "cycling_minutes, distance_km "
        "FROM travel_matrix WHERE city = ?",
        (city,)
    ).fetchall()

    matrix: dict = {}
    for row in matrix_rows:
        key = f"{row['venue_id_a']}_to_{row['venue_id_b']}"
        transit_min = row["transit_minutes"] or 0
        matrix[key] = {
            "walking":  row["walk_minutes"],
            "transit":  transit_min,
            "cycling":  row["cycling_minutes"],
            "taxi":     round(transit_min * 0.85, 1),   # approximation for London
            "distance_km": row["distance_km"],
        }

    # Load truth carriers: {venue_id: [doc_id, ...]} for wrong-info venues
    truth_carriers: dict = {}
    try:
        tc_rows = conn.execute(
            "SELECT venue_id, doc_id FROM doc_venue_roles WHERE role = 'truth_carrier'"
        ).fetchall()
        for row in tc_rows:
            truth_carriers.setdefault(row["venue_id"], []).append(row["doc_id"])
    except Exception:
        pass   # table may not exist in older DBs

    conn.close()

    if not matrix:
        print(
            f"  ⚠ WARNING: travel_matrix table is empty for '{city}'. "
            f"Run: python scripts/generation/build_travel_matrix.py --city {city}. "
            f"Travel time F-score checks will be skipped.",
            file=_sys.stderr
        )

    return venues, matrix, truth_carriers


def load_weather_forecast(city: str) -> dict:
    """Returns {date_str: {condition, temp_c, rain_mm, ...}} or {} if unavailable."""
    path = DATA_DIR / "ground_truth" / city / "weather_forecast.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    # File format: {"paris": {"2025-03-07": {...}, ...}}
    # Return the inner date-keyed dict directly
    return data.get(city.lower(), data)  # fallback to raw if no city wrapper

def load_events(city: str) -> list:
    """Returns list of event dicts, or [] if no events file."""
    path = DATA_DIR / "ground_truth" / city / "events.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def get_active_event(events: list, venue_id: str, date_str: str) -> dict | None:
    """Return the first event active for venue_id on date_str, or None."""
    for evt in events:
        if evt.get("venue_id") != venue_id:
            continue
        if evt.get("date_start","") <= date_str <= evt.get("date_end",""):
            event_days = evt.get("event_days")
            if event_days:
                from datetime import date as _date
                try:
                    dow = _date.fromisoformat(date_str).weekday()
                    day_map = {"mon":0,"tue":1,"wed":2,"thu":3,"fri":4,"sat":5,"sun":6}
                    allowed = [day_map[d[:3]] for d in event_days if d[:3] in day_map]
                    if dow not in allowed:
                        continue
                except Exception:
                    pass
            return evt
    return None


def load_task(task_id: str, city: str = None, run_name: str = None) -> dict:
    """Load a task JSON by task_id.

    Search order:
      1. data/cities/{city}/tasks/ tree (if city provided)
      2. Scan all data/cities/*/tasks/ trees
    """
    # 1. City-specific search
    if city:
        _city = city.lower()
        _tasks_root = DATA_DIR / "cities" / _city / "tasks"
        if _tasks_root.exists():
            for f in _tasks_root.rglob(f"{task_id}.json"):
                if "agent_log" not in str(f):
                    return json.loads(f.read_text())

    # 2. Scan all cities
    _cities_root = DATA_DIR / "cities"
    if _cities_root.exists():
        for f in _cities_root.rglob(f"{task_id}.json"):
            if "agent_log" not in str(f):
                return json.loads(f.read_text())

    raise FileNotFoundError(
        f"Task '{task_id}' not found. Searched: data/cities/{city or '*'}/tasks/"
    )

DAY_NAME_TO_KEY = {
    "monday":"mon","tuesday":"tue","wednesday":"wed",
    "thursday":"thu","friday":"fri","saturday":"sat","sunday":"sun",
}

def date_to_day_key(date_str: str) -> str:
    d = date.fromisoformat(date_str)
    return ["mon","tue","wed","thu","fri","sat","sun"][d.weekday()]

def get_day_date(task: dict, day_index: int) -> tuple[str, str]:
    """Returns (date_str, day_key) for day_index (0-based)."""
    start   = date.fromisoformat(task["start_date"])
    d       = start + timedelta(days=day_index)
    day_key = ["mon","tue","wed","thu","fri","sat","sun"][d.weekday()]
    return d.isoformat(), day_key

VALID_TIME_RE       = re.compile(r"^\d{2}:\d{2}$")
VALID_ACTIVITY_TYPES = {"meal", "visit", "leisure", "shopping", "transport"}


# ─────────────────────────────────────────────────────────────────────────────
# HOURS HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_hours_windows(hours_list):
    """Parse hours into (open_min, close_min, open_str, close_str) tuples.
    Supports combined "HH:MM-HH:MM" and legacy split-pair ["HH:MM","HH:MM"]."""
    windows = []
    has_combined = any(isinstance(s, str) and '-' in s[1:] for s in hours_list)
    if has_combined:
        for slot in hours_list:
            if isinstance(slot, str) and '-' in slot[1:]:
                dash = slot.index('-', 1)
                open_str, close_str = slot[:dash], slot[dash+1:]
                o = hhmm_to_min(open_str); c = hhmm_to_min(close_str)
                if c <= o: c += 24 * 60
                windows.append((o, c, open_str, close_str))
    else:
        for open_str, close_str in zip(hours_list[0::2], hours_list[1::2]):
            o = hhmm_to_min(open_str); c = hhmm_to_min(close_str)
            if c <= o: c += 24 * 60
            windows.append((o, c, open_str, close_str))
    return windows


def _is_open_at(hours_list: list[str], ts_min: int, te_min: int) -> tuple[bool, str]:
    """Check [ts_min, te_min] against hours windows. Supports combined and split-pair formats."""
    if not hours_list:
        return False, "No valid hours data"
    windows = _parse_hours_windows(hours_list)
    if not windows:
        return False, "No valid hours data"
    for window in windows:
        o, c, open_str, close_str = window
        if ts_min >= o and te_min <= c:
            return True, f"{open_str}–{close_str}"
    return False, f"outside hours (windows: {[(w[2],w[3]) for w in windows]})"


def _get_first_open_min(hours_list: list[str]) -> Optional[int]:
    """Return the opening minute of the first window, or None if no hours."""
    if not hours_list:
        return None
    windows = _parse_hours_windows(hours_list)
    return windows[0][0] if windows else None


def evaluate_c_score(result: dict, task: dict, venues: dict) -> dict:
    """
    P22 redesign (BFCL-style AST validation + proportional deductions).

    Hard gates (score=0, F and P still run independently):
      - Zero tool calls made
      - No <final_plan> produced / JSON parse failed

    Section A — Per-call AST validation (-0.04 each):
      A1  Unknown tool name
      A2  Missing required parameter
      A3  Unexpected parameter (parameter hallucination)
      A4  Wrong parameter type
      A5  city param != task city
      A6  venue_id param contains spaces (name passed instead of ID)
      A7  venue_id / from_venue_id / to_venue_id not in prior tool results (sequential)

    Section B — DELETED (process completeness checks removed per P22)

    Section C — Timetable format (-0.04 each):
      C1  task_id missing or doesn't match task
      C2  city missing or doesn't match task city
      C3  days field missing or not a list
      C4  Fewer days than expected
      C5  More days than expected
      C6  day_of_week missing or invalid
      C7  Day has no activities
      C8  Activity missing required field (per field)
      C9  time_start or time_end not HH:MM
      C10 time_end <= time_start for non-overnight activity
      C11 Invalid activity_type
      C12 Invalid transport mode
      C13 venue_id not seen in any tool result (traceability; was warning)

    Section D — Blank time coverage (variable deduction, max 0.8 per day):
      day_ref      = min(last_end - first_start, DEFAULT_DAY_MIN=600)
      expected_cnt = day_ref / VENUE_TIME_COST=120
      deduction    = 0.8 / expected_cnt * (expected_cnt - actual_cnt)
                     when actual_cnt < floor(expected_cnt) - 1

    Informational warnings (no score impact):
      - total_estimated_cost_local mismatch vs sum of activities
      - Active event venue scheduled without a dated get_official_site call
    """
    tool_log = result.get("tool_call_log", [])
    plan     = result.get("parsed_plan")

    # ── CONSTANTS ─────────────────────────────────────────────────────────────
    KNOWN_TOOLS = {
        "search_yelp", "search_blogs_and_forums",
        "get_official_site", "get_travel_time", "THINK",
    }
    REQUIRED_PARAMS = {
        "search_yelp":               ["query", "city"],
        "search_blogs_and_forums":   ["city"],
        "get_official_site":         ["venue_id", "city"],
        "get_travel_time":           ["from_venue_id", "to_venue_id", "city"],
        "THINK":                     ["thought"],
    }
    OPTIONAL_PARAMS = {
        "search_yelp":             {"category", "top_k"},
        "search_blogs_and_forums": {"query", "venue_name", "top_k"},
        "get_official_site":       {"date"},
        "get_travel_time":         set(),
        "THINK":                   set(),
    }
    INT_PARAMS            = {"top_k"}
    STR_PARAMS            = {"query", "city", "venue_id",
                              "from_venue_id", "to_venue_id", "venue_name", "category"}
    VALID_TRANSPORT_MODES = {"walking", "transit", "cycling", "taxi"}
    DEFAULT_DAY_MIN       = 600   # 10 hours reference window
    VENUE_TIME_COST       = 120   # 2 hours per venue slot
    GAP_THRESHOLD         = 90    # min — a genuine idle block, not a transit gap (P6-T7)

    task_city  = task.get("city", "").lower()
    deductions = []   # (amount: float, description: str)
    warnings   = []   # informational only — no score impact

    # ── HARD GATE 1: no tool calls ─────────────────────────────────────────────
    if not tool_log:
        return {
            "score": 0.0, "passed": False,
            "deductions": [(1.0, "Hard gate: agent called zero tools")],
            "warnings": [],
            "tools_used": [],
            "official_site_called": [],
        }

    # ── SECTION A: Per-call AST validation ────────────────────────────────────
    tools_used           = set()
    venue_ids_from_tools = set()   # grows after each call — sequential tracking
    official_site_called = set()

    for i, call in enumerate(tool_log):
        tool_name   = call.get("tool_name", "")
        tool_input  = call.get("tool_input",  {}) or {}
        tool_output = call.get("tool_output", {}) or {}

        # A1: unknown tool
        if tool_name not in KNOWN_TOOLS:
            deductions.append((0.04, f"[call {i+1}] Unknown tool: '{tool_name}'"))
            continue

        if tool_name == "THINK":
            if "thought" not in tool_input:
                deductions.append((0.04, f"[call {i+1}] THINK: missing required param 'thought'"))
            continue

        tools_used.add(tool_name)

        # A2: missing required params
        for param in REQUIRED_PARAMS.get(tool_name, []):
            if param not in tool_input:
                deductions.append((0.04, f"[call {i+1}] {tool_name}: missing required param '{param}'"))

        # A3: parameter hallucination (unexpected params)
        all_valid = (set(REQUIRED_PARAMS.get(tool_name, []))
                     | OPTIONAL_PARAMS.get(tool_name, set()))
        for param in tool_input:
            if param not in all_valid:
                deductions.append((0.04,
                    f"[call {i+1}] {tool_name}: unexpected param '{param}' (hallucination)"))

        # A4: wrong param types
        for param, val in tool_input.items():
            if param in INT_PARAMS and not isinstance(val, int):
                deductions.append((0.04,
                    f"[call {i+1}] {tool_name}: '{param}' should be int, got {type(val).__name__}"))
            if param in STR_PARAMS and not isinstance(val, str):
                deductions.append((0.04,
                    f"[call {i+1}] {tool_name}: '{param}' should be str, got {type(val).__name__}"))

        # A5: city mismatch
        city_val = tool_input.get("city")
        if isinstance(city_val, str) and city_val.lower() != task_city:
            deductions.append((0.04,
                f"[call {i+1}] {tool_name}: city='{city_val}' doesn't match task city '{task_city}'"))

        # A6: venue param looks like a name (spaces in get_travel_time ID params)
        if tool_name == "get_travel_time":
            for param in ["from_venue_id", "to_venue_id"]:
                val = tool_input.get(param, "")
                if isinstance(val, str) and " " in val:
                    deductions.append((0.04,
                        f"[call {i+1}] get_travel_time: '{param}' looks like a name, not an ID: '{val}'"))

        # A7: venue_id params not in prior tool results (sequential check)
        if venue_ids_from_tools:
            if tool_name == "get_official_site":
                vid = tool_input.get("venue_id")
                if vid and vid not in venue_ids_from_tools:
                    deductions.append((0.04,
                        f"[call {i+1}] get_official_site: venue_id '{vid}' not seen in prior tool results"))
            if tool_name == "get_travel_time":
                for param in ["from_venue_id", "to_venue_id"]:
                    vid = tool_input.get(param)
                    if vid and vid not in venue_ids_from_tools:
                        deductions.append((0.04,
                            f"[call {i+1}] get_travel_time: {param}='{vid}' not seen in prior tool results"))

        # Track official site calls
        if tool_name == "get_official_site":
            vid = tool_input.get("venue_id")
            if vid:
                official_site_called.add(vid)

        # Collect venue IDs from this call's OUTPUT (available to subsequent calls)
        if isinstance(tool_output, dict):
            for r in tool_output.get("results", []):
                if isinstance(r, dict) and "venue_id" in r:
                    venue_ids_from_tools.add(r["venue_id"])
            if "venue_id" in tool_output:
                venue_ids_from_tools.add(tool_output["venue_id"])

    # ── HARD GATE 2: no plan ──────────────────────────────────────────────────
    if plan is None:
        return {
            "score": 0.0, "passed": False,
            "deductions": [(1.0, "Hard gate: no <final_plan> produced or JSON parse failed")],
            "warnings": warnings,
            "tools_used": sorted(tools_used),
            "official_site_called": sorted(official_site_called),
        }

    # ── SECTION C: Timetable format ───────────────────────────────────────────

    # C1: task_id
    if "task_id" not in plan:
        deductions.append((0.04, "Plan missing field 'task_id'"))
    elif plan["task_id"] != task.get("task_id", ""):
        deductions.append((0.04,
            f"Plan task_id '{plan['task_id']}' doesn't match task '{task.get('task_id')}'"))

    # C2: city
    if "city" not in plan:
        deductions.append((0.04, "Plan missing field 'city'"))
    elif plan.get("city", "").lower() != task_city:
        deductions.append((0.04,
            f"Plan city '{plan.get('city')}' doesn't match task city '{task_city}'"))

    # C3/C4/C5: days list
    days = plan.get("days", [])
    if "days" not in plan:
        deductions.append((0.04, "Plan missing field 'days'"))
    elif not isinstance(days, list) or len(days) == 0:
        deductions.append((0.04, "'days' is empty or not a list"))
    else:
        expected_days = task.get("days", 1)
        if len(days) < expected_days:
            deductions.append((0.04,
                f"Plan has {len(days)} day(s), expected {expected_days}"))
        if len(days) > expected_days:
            deductions.append((0.04,
                f"Plan has {len(days)} day(s), more than expected {expected_days}"))

        for day_idx, day in enumerate(days):
            # C6: day_of_week
            dow = day.get("day_of_week", "")
            if dow not in DAY_NAME_TO_KEY.values():
                deductions.append((0.04,
                    f"Day {day_idx+1}: 'day_of_week' missing or invalid: '{dow}'"))

            activities = day.get("activities", [])

            # C7: no activities
            if not isinstance(activities, list) or len(activities) == 0:
                deductions.append((0.04, f"Day {day_idx+1}: no activities"))
                continue

            for j, act in enumerate(activities):
                ref   = f"Day {day_idx+1} act {j+1}"
                atype = act.get("activity_type", "")

                # C8: required fields
                if atype == "transport":
                    _required = ["time_start", "time_end", "activity_type",
                                 "mode", "from_venue_id", "to_venue_id"]
                else:
                    _required = ["time_start", "time_end", "venue_id",
                                 "venue_name", "activity_type"]
                for field in _required:
                    if field not in act:
                        deductions.append((0.04, f"{ref}: missing field '{field}'"))

                ts = act.get("time_start", "")
                te = act.get("time_end",   "")

                # C9: time format
                if ts and not VALID_TIME_RE.match(ts):
                    deductions.append((0.04, f"{ref}: time_start '{ts}' not in HH:MM format"))
                if te and not VALID_TIME_RE.match(te):
                    deductions.append((0.04, f"{ref}: time_end '{te}' not in HH:MM format"))

                # C10: time ordering (non-overnight only)
                if VALID_TIME_RE.match(ts) and VALID_TIME_RE.match(te):
                    if (hhmm_to_min(te) <= hhmm_to_min(ts)
                            and hhmm_to_min(ts) < 18 * 60):
                        deductions.append((0.04,
                            f"{ref}: time_end '{te}' not after time_start '{ts}'"))

                # C11: invalid activity_type
                if atype and atype not in VALID_ACTIVITY_TYPES:
                    deductions.append((0.04, f"{ref}: invalid activity_type '{atype}'"))

                # C12: invalid transport mode
                if atype == "transport":
                    mode_val = act.get("mode", "")
                    if mode_val and mode_val not in VALID_TRANSPORT_MODES:
                        deductions.append((0.04,
                            f"{ref}: invalid transport mode '{mode_val}'"))

                # C13: venue traceability (promoted from warning)
                vid = act.get("venue_id", "")
                if (atype != "transport" and vid
                        and venue_ids_from_tools
                        and vid not in venue_ids_from_tools):
                    deductions.append((0.04,
                        f"{ref}: venue_id '{vid}' not seen in any tool result"))

    # ── Cost consistency (warning only — no score impact) ─────────────────────
    stated_total = plan.get("total_estimated_cost_local")
    if stated_total is not None:
        computed_total = sum(
            a.get("estimated_cost_local") or 0
            for d in plan.get("days", [])
            for a in d.get("activities", [])
        )
        tolerance = max(5.0, computed_total * 0.10)
        if abs(stated_total - computed_total) > tolerance:
            warnings.append(
                f"total_estimated_cost_local mismatch: "
                f"stated={stated_total:.0f} vs computed={computed_total:.0f} "
                f"(diff {abs(stated_total - computed_total):.0f})"
            )

    # ── Events check (warning only) ───────────────────────────────────────────
    plan_venue_ids  = {
        act.get("venue_id", "")
        for d in plan.get("days", [])
        for act in d.get("activities", [])
    }
    city_for_events = plan.get("city", task.get("city", "paris"))
    events_list     = load_events(city_for_events)
    task_start      = task.get("start_date", "")
    task_days_n     = task.get("days", 1)
    try:
        trip_dates = [
            (date.fromisoformat(task_start) + timedelta(days=k)).isoformat()
            for k in range(task_days_n)
        ]
    except Exception:
        trip_dates = [task_start] if task_start else []

    for vid in plan_venue_ids:
        for trip_date in trip_dates:
            evt = get_active_event(events_list, vid, trip_date)
            if evt:
                called_with_date = any(
                    tc.get("tool_name") == "get_official_site"
                    and tc.get("tool_input", {}).get("venue_id") == vid
                    and tc.get("tool_input", {}).get("date")
                    for tc in tool_log
                )
                if not called_with_date:
                    vname = venues.get(vid, {}).get("name", vid)
                    warnings.append(
                        f"'{vname}' has active event on {trip_date} ('{evt['name']}') "
                        f"— call get_official_site with date='{trip_date}' for correct info"
                    )
                break

    # ── SECTION D: Blank time coverage ────────────────────────────────────────
    for day_idx, day in enumerate(plan.get("days", [])):
        non_transport = [
            a for a in day.get("activities", [])
            if a.get("activity_type") != "transport"
        ]
        if not non_transport:
            continue   # already penalised by C7

        starts = [hhmm_to_min(a["time_start"]) for a in non_transport
                  if VALID_TIME_RE.match(a.get("time_start", ""))]
        ends   = [hhmm_to_min(a["time_end"])   for a in non_transport
                  if VALID_TIME_RE.match(a.get("time_end", ""))]

        if not starts or not ends:
            continue   # invalid times already caught by C9

        # Compute effective ends (handle overnight: end < start)
        eff_ends = []
        for a in non_transport:
            ts_str = a.get("time_start", "")
            te_str = a.get("time_end", "")
            if not (VALID_TIME_RE.match(ts_str) and VALID_TIME_RE.match(te_str)):
                continue
            ts_m = hhmm_to_min(ts_str)
            te_m = hhmm_to_min(te_str)
            eff_ends.append(te_m if te_m >= ts_m else te_m + 24 * 60)

        if not eff_ends:
            continue

        # day_ref represents the active time budget for the day.
        # For type2 tasks the budget is the time ceiling; otherwise use the
        # 10-hour reference. This is the TOTAL time the day is supposed to
        # span — NOT capped at raw_span — so a 1-activity-all-day plan is
        # correctly compared against the full reference budget (P6-T7).
        qr = task.get("public_input", {}).get("query_resources", {}) or {}
        tc = qr.get("time_ceiling_minutes")
        day_ref = tc if isinstance(tc, (int, float)) and tc > 0 else DEFAULT_DAY_MIN

        expected_cnt = day_ref / VENUE_TIME_COST
        actual_cnt   = len(non_transport)

        # max_gap: largest visible idle gap between consecutive activities.
        # Single-activity special case: no consecutive pair exists, so treat
        # the entire budget as one gap (a 1-activity-all-day plan is correctly
        # flagged as sparse).
        sorted_acts = sorted(
            non_transport,
            key=lambda a: hhmm_to_min(a.get("time_start", "00:00"))
                          if VALID_TIME_RE.match(a.get("time_start", "")) else 0
        )
        if len(sorted_acts) == 1:
            max_gap = int(day_ref)
        else:
            gaps = []
            for i in range(len(sorted_acts) - 1):
                te_str = sorted_acts[i].get("time_end", "")
                ts_str = sorted_acts[i + 1].get("time_start", "")
                if VALID_TIME_RE.match(te_str) and VALID_TIME_RE.match(ts_str):
                    g = hhmm_to_min(ts_str) - hhmm_to_min(te_str)
                    if g >= 0:
                        gaps.append(g)
            max_gap = max(gaps) if gaps else 0

        # Gap-based deduction (P6-T7): only fire when there is visible idle
        # time between activities. Trailing time after the last activity is
        # ambiguous (other commitments may exist) and is intentionally not
        # penalised. Deduction is a flat 0.04 per missing slot to stay
        # consistent with other C-score per-issue deductions.
        if actual_cnt < math.floor(expected_cnt) and max_gap > GAP_THRESHOLD:
            missing   = math.floor(expected_cnt) - actual_cnt
            deduction = round(missing * 0.04, 3)
            deductions.append((
                deduction,
                f"Day {day_idx+1}: sparse — {actual_cnt} activities, "
                f"{max_gap}-min idle gap (expected ~{math.floor(expected_cnt)} "
                f"in {day_ref // 60:.0f}h budget)"
            ))

        # ── Section D2: gratuitous duplicate venues (P6-T9 Scenario A) ─────
        # F4a already sums same-venue durations on split visits, so this is
        # purely informational — no score impact.
        from collections import Counter as _Counter
        _vid_counts = _Counter(
            a.get("venue_id") for a in non_transport
            if a.get("venue_id") and a.get("activity_type") != "transport"
        )
        for _vid, _n in _vid_counts.items():
            if _n > 1:
                warnings.append(
                    f"Day {day_idx+1}: venue_id '{_vid}' appears {_n} times. "
                    f"If this is a split visit (e.g. morning + afternoon at a "
                    f"museum), this is fine; F4a already sums the durations. "
                    f"Otherwise consider whether the repeat is intentional."
                )

    # ── FINAL SCORE ───────────────────────────────────────────────────────────
    total_deduction = sum(d[0] for d in deductions)
    score  = max(0.0, round(1.0 - total_deduction, 3))
    passed = True   # both hard gates cleared; F and P always run

    return {
        "score":                score,
        "passed":               passed,
        "deductions":           deductions,
        "warnings":             warnings,
        "tools_used":           sorted(tools_used),
        "official_site_called": sorted(official_site_called),
    }




# ─────────────────────────────────────────────────────────────────────────────
# TIER 2: F-SCORE — Feasibility
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_f_score(result: dict, task: dict, venues: dict, matrix: dict,
                     truth_carriers: dict = None) -> dict:
    """
    P22-F redesign: deduction-based, no hard fails.

    F = max(0.0, 1.0 − Σ deductions)

    F1 — Temporal structure
      F1a  Activity overlap                         −0.20 per pair
      F1b  Travel time infeasible (merged F3a)       −0.15 per pair
             explicit transport: leg duration < matrix time for mode
             implicit (no transport activity): gap < matrix walking time

    F2 — Venue access
      F2a  Opening hours violation                   −0.15 per venue
      F2b  Ticket sold out                           −0.15 per venue
      F2c  Truth-carrier not retrieved               −0.05 per wrong-info venue
      F2d  Event capacity sold out                   −0.15 per venue

    F3 — Logistics (implicit travel pairs only)
      F3b  Buffer tiers (gap − walking_time):
             < 5 min  → −0.10
             5–12 min → −0.06
             12–15 min → −0.03
             15–30 min → no deduction  (safe zone)
             > 30 min  → −0.02  (over-spaced)

    F4 — Scheduling quality
      F4a  Visit duration:
             under-scheduled (<50% recommended) → −0.05 per venue
             over-scheduled (>rec + max(60,rec×50%)) → −0.02 per venue

    F2e — Time-ceiling enforcement (P6-T4 Check A + T4b) — runs ACROSS
    all days, not inside the per-day loop. Branches on `ceiling_mode`
    and `ceiling_scope` in query_resources:
      type2 + contiguous (+ start_time):
        sum across days > ceiling → −0.30
        + each activity outside [start, start+ceiling] → −0.15
      type2 + spread + total (default for spread):
        sum across days > ceiling → −0.30
      type2 + spread + per_day:
        each day's sum > ceiling → −0.30 per day
      non-type2 with ceiling (forward-looking, no such task today):
        per-day check; each day's sum > ceiling → −0.30 per day

    has_critical_issues = any deduction ≥ 0.10 (F1a, F1b, F2a-d, F2e, extreme F3b)
    """
    if truth_carriers is None:
        truth_carriers = {}

    plan = result.get("parsed_plan")
    if plan is None:
        return {"score": 0.0, "has_critical_issues": True,
                "deductions": [{"section": "gate", "amount": 1.0,
                                 "reason": "No plan to evaluate"}]}

    tool_log = result.get("tool_call_log", [])
    days     = plan.get("days", [])
    city     = task.get("city", "paris")
    events   = load_events(city)
    deductions = []   # {"section", "amount", "reason"}

    def _ded(section, amount, reason):
        deductions.append({"section": section, "amount": amount, "reason": reason})

    def _gap(te_str: str, ts_str: str) -> int:
        """Gap in minutes between te and ts, handling overnight."""
        gap = hhmm_to_min(ts_str) - hhmm_to_min(te_str)
        if gap < 0:
            gap += 24 * 60
        return gap

    def _travel_time(v1: str, v2: str, mode: str = "walking") -> Optional[float]:
        """Get travel time from matrix for given mode. Falls back to walking."""
        entry = matrix.get(f"{v1}_to_{v2}") or matrix.get(f"{v2}_to_{v1}")
        if entry is None:
            return None
        if isinstance(entry, dict):
            return entry.get(mode) or entry.get("walking")
        return float(entry)

    # F2c: per-venue (task-level) check — track venues already evaluated so
    # that multi-day or multi-activity visits to the same wrong-info venue
    # don't multiply the deduction. (The truth-carrier retrieval is a single
    # binary action by the agent across the whole task.)
    f2c_checked_task: set = set()

    for day_idx, day in enumerate(days):
        date_str, day_key = get_day_date(task, day_idx)
        activities = day.get("activities", [])

        # Sort by start time for overlap and buffer checks
        def _sort_key(a):
            ts = a.get("time_start", "00:00")
            return hhmm_to_min(ts) if VALID_TIME_RE.match(ts) else 0
        acts = sorted(activities, key=_sort_key)

        # ── F1a: Overlap ─────────────────────────────────────────────────────
        for j in range(len(acts) - 1):
            a1, a2 = acts[j], acts[j + 1]
            ts1 = a1.get("time_start", "")
            te1 = a1.get("time_end",   "")
            ts2 = a2.get("time_start", "")
            if not (VALID_TIME_RE.match(ts1) and VALID_TIME_RE.match(te1)
                    and VALID_TIME_RE.match(ts2)):
                continue
            gap = _gap(te1, ts2)
            if gap < 0 or hhmm_to_min(ts2) < hhmm_to_min(te1):
                # True overlap (not overnight)
                if hhmm_to_min(ts1) < 20 * 60:   # skip late-night edge cases
                    n1 = a1.get("venue_name", a1.get("venue_id", "?"))
                    n2 = a2.get("venue_name", a2.get("venue_id", "?"))
                    _ded("F1a", 0.20,
                         f"Day {day_idx+1}: '{n1}' ends {te1} overlaps '{n2}' starts {ts2}")

        # Build list of venue activities (non-transport) for per-venue checks
        venue_acts = [(j, a) for j, a in enumerate(acts)
                      if a.get("activity_type") != "transport"
                      and a.get("venue_id", "") in venues]

        # Accumulate per-venue duration for F4a (P6-T9): split visits to the
        # same venue on the same day are summed before bounds checking.
        from collections import defaultdict as _dd
        venue_total_minutes: dict = _dd(int)

        # (F2c is tracked at task-level above via f2c_checked_task.)

        # ── Per-activity checks (F2a, F2b, F2c, F2d) ───────────────────────
        for _, act in venue_acts:
            vid    = act.get("venue_id", "")
            ts_str = act.get("time_start", "")
            te_str = act.get("time_end",   "")
            venue  = venues[vid]
            vname  = venue.get("name", vid)

            if not (VALID_TIME_RE.match(ts_str) and VALID_TIME_RE.match(te_str)):
                continue

            ts_min = hhmm_to_min(ts_str)
            te_min = hhmm_to_min(te_str)

            # Accumulate for F4a (after this loop)
            venue_total_minutes[vid] += (te_min - ts_min if te_min > ts_min
                                          else te_min - ts_min + 1440)

            # Resolve active event overrides
            active_evt    = get_active_event(events, vid, date_str)
            evt_overrides = active_evt.get("overrides", {}) if active_evt else {}
            eff_hours     = evt_overrides.get("hours") or venue.get("hours", {})

            # F2a: Opening hours
            day_hours = eff_hours.get(day_key)
            if day_hours is None:
                _ded("F2a", 0.15,
                     f"Day {day_idx+1}: '{vname}' CLOSED on {day_key} "
                     f"(scheduled {ts_str}–{te_str})")
            else:
                ok, reason = _is_open_at(day_hours, ts_min, te_min)
                if not ok:
                    _ded("F2a", 0.15,
                         f"Day {day_idx+1}: '{vname}' {reason} — "
                         f"scheduled {ts_str}–{te_str}")

            # F2b: Ticket availability
            ticket_avail = venue.get("ticket_availability", {})
            date_avail   = ticket_avail.get(date_str)
            is_sold_out  = (
                (isinstance(date_avail, dict) and date_avail.get("sold_out")) or
                date_avail == "sold_out"
            )
            if is_sold_out:
                _ded("F2b", 0.15,
                     f"Day {day_idx+1}: '{vname}' SOLD OUT on {date_str}")

            # F2c: Truth-carrier not retrieved (per-venue, task-level).
            # The agent's choice to retrieve happens once; same venue scheduled
            # multiple times (same day or across days) should not multiply
            # the deduction.
            if (venue.get("has_wrong_info") or venue.get("has_wrong_info_planned")) \
                    and vid not in f2c_checked_task:
                f2c_checked_task.add(vid)
                carrier_docs = truth_carriers.get(vid, [])
                retrieved = False
                for call in tool_log:
                    if call.get("tool_name") != "search_blogs_and_forums":
                        continue
                    for r in call.get("tool_output", {}).get("results", []):
                        if r.get("doc_id") in carrier_docs:
                            retrieved = True
                            break
                    if retrieved:
                        break
                # Also accept get_official_site as verification
                if not retrieved:
                    retrieved = any(
                        c.get("tool_name") == "get_official_site"
                        and c.get("tool_input", {}).get("venue_id") == vid
                        for c in tool_log
                    )
                if not retrieved:
                    _ded("F2c", 0.05,
                         f"Day {day_idx+1}: '{vname}' has wrong info but agent "
                         f"never retrieved truth-carrier document")

            # F2d: Event capacity sold out (weekend evening)
            if active_evt and evt_overrides.get("capacity_limited"):
                if evt_overrides.get("weekend_evening_sold_out"):
                    if day_key in ("fri", "sat") and ts_min >= hhmm_to_min("17:00"):
                        _ded("F2d", 0.15,
                             f"Day {day_idx+1}: '{vname}' event '{active_evt['name']}' "
                             f"sold out for weekend evening slot")

        # ── F4a: Visit duration — per-venue, per-day totals (P6-T9) ────────
        # Sum durations across split visits before applying the bounds check,
        # so a morning + afternoon return to the same venue is scored against
        # the combined time, not each visit independently.
        for vid, total in venue_total_minutes.items():
            venue = venues[vid]
            vname = venue.get("name", vid)
            active_evt    = get_active_event(events, vid, date_str)
            evt_overrides = active_evt.get("overrides", {}) if active_evt else {}
            eff_rec_min   = (evt_overrides.get("visit_duration_minutes")
                             or venue.get("recommended_visit_minutes"))
            if not (eff_rec_min and eff_rec_min > 0):
                continue
            lower = eff_rec_min * 0.5
            upper = eff_rec_min + max(60, eff_rec_min * 0.5)
            if total < lower:
                _ded("F4a", 0.05,
                     f"Day {day_idx+1}: '{vname}' under-scheduled {total:.0f}min "
                     f"(min {lower:.0f}, rec {eff_rec_min}min)")
            elif total > upper:
                _ded("F4a", 0.02,
                     f"Day {day_idx+1}: '{vname}' over-scheduled {total:.0f}min "
                     f"(max {upper:.0f}, rec {eff_rec_min}min)")

        # ── F1b + F3b: consecutive venue-pair travel checks ──────────────────
        for idx_pair in range(len(venue_acts) - 1):
            j1, a1 = venue_acts[idx_pair]
            j2, a2 = venue_acts[idx_pair + 1]
            v1  = a1.get("venue_id", "")
            v2  = a2.get("venue_id", "")
            te1 = a1.get("time_end",   "")
            ts2 = a2.get("time_start", "")

            if v1 == v2 or not (VALID_TIME_RE.match(te1) and VALID_TIME_RE.match(ts2)):
                continue

            n1 = venues.get(v1, {}).get("name", v1)
            n2 = venues.get(v2, {}).get("name", v2)

            # Find explicit transport activities between these two venue acts
            transport_between = [acts[k] for k in range(j1 + 1, j2)
                                  if acts[k].get("activity_type") == "transport"]

            if transport_between:
                # F1b: explicit transport — check duration vs matrix
                ta     = transport_between[0]
                mode   = ta.get("mode", "walking")
                t_start = ta.get("time_start", "")
                t_end   = ta.get("time_end",   "")
                if VALID_TIME_RE.match(t_start) and VALID_TIME_RE.match(t_end):
                    t_dur    = _gap(t_start, t_end)
                    required = _travel_time(v1, v2, mode)
                    if required is not None and t_dur < required:
                        _ded("F1b", 0.15,
                             f"Day {day_idx+1}: {mode} leg '{n1}'→'{n2}' "
                             f"allocated {t_dur}min, needs {required:.0f}min")
            else:
                # Implicit travel — use walking time
                gap        = _gap(te1, ts2)
                walk_time  = _travel_time(v1, v2, "walking")
                if walk_time is None:
                    continue

                # F1b: gap < walk time (physically impossible)
                if gap < walk_time:
                    _ded("F1b", 0.15,
                         f"Day {day_idx+1}: gap '{n1}'→'{n2}' is {gap}min "
                         f"but walk takes {walk_time:.0f}min")
                    continue   # don't double-count with buffer

                # F3b: buffer tiers
                buffer = gap - walk_time
                if buffer < 5:
                    _ded("F3b", 0.10,
                         f"Day {day_idx+1}: buffer '{n1}'→'{n2}' {buffer:.0f}min "
                         f"(< 5min — no margin)")
                elif buffer < 12:
                    _ded("F3b", 0.06,
                         f"Day {day_idx+1}: buffer '{n1}'→'{n2}' {buffer:.0f}min "
                         f"(tight, < 12min)")
                elif buffer < 15:
                    _ded("F3b", 0.03,
                         f"Day {day_idx+1}: buffer '{n1}'→'{n2}' {buffer:.0f}min "
                         f"(slightly rushed, < 15min)")
                elif buffer > 30:
                    _ded("F3b", 0.02,
                         f"Day {day_idx+1}: buffer '{n1}'→'{n2}' {buffer:.0f}min "
                         f"(over-spaced, > 30min)")

    # ── F2e: Time-ceiling enforcement (P6-T4 Check A + T4b) — cross-day ───
    # Branches on type, ceiling_mode, ceiling_scope:
    #   type2 + contiguous + start_time:
    #      sum check (−0.30 if trip total > ceiling) +
    #      window check (−0.15 per activity outside [start, start+ceiling])
    #   type2 + contiguous (no start_time):  sum check only
    #   type2 + spread + total:    sum across days > ceiling → −0.30
    #   type2 + spread + per_day:  per-day sum > ceiling → −0.30 per day
    #   non-type2 with ceiling:    per-day sum > ceiling → −0.30 per day
    #                              (forward-looking — no non-type2 task carries
    #                              the field today; ready for T10).
    qr = task.get("public_input", {}).get("query_resources", {}) or {}
    ceiling = qr.get("time_ceiling_minutes")
    if isinstance(ceiling, (int, float)) and ceiling > 0:
        structural    = str(task.get("structural_type") or "")
        is_type2      = structural.startswith("type2")
        ceiling_mode  = qr.get("ceiling_mode",  "contiguous")
        ceiling_scope = qr.get("ceiling_scope", "total")
        start_time    = qr.get("start_time")

        def _day_engagement(day) -> float:
            """Sum non-transport activity durations + implicit travel +
            explicit transport durations for one day."""
            non_transport = sorted(
                [a for a in day.get("activities", [])
                 if a.get("activity_type") != "transport"
                 and a.get("venue_id", "") in venues],
                key=lambda a: hhmm_to_min(a.get("time_start", "00:00"))
                              if VALID_TIME_RE.match(a.get("time_start", "")) else 0,
            )
            total = 0.0
            for i, a in enumerate(non_transport):
                ts = a.get("time_start", ""); te = a.get("time_end", "")
                if not (VALID_TIME_RE.match(ts) and VALID_TIME_RE.match(te)):
                    continue
                ts_m, te_m = hhmm_to_min(ts), hhmm_to_min(te)
                total += (te_m - ts_m) if te_m > ts_m else (te_m - ts_m + 1440)
                if i + 1 < len(non_transport):
                    nxt = non_transport[i + 1]
                    v1 = a.get("venue_id", "")
                    v2 = nxt.get("venue_id", "")
                    if v1 and v2 and v1 != v2:
                        walk = _get_walk_minutes(v1, v2, matrix)
                        if walk is not None:
                            total += walk
            # Also count explicit transport activities (the agent's stated
            # travel time, which may differ from matrix walking).
            for a in day.get("activities", []):
                if a.get("activity_type") != "transport":
                    continue
                ts = a.get("time_start", ""); te = a.get("time_end", "")
                if not (VALID_TIME_RE.match(ts) and VALID_TIME_RE.match(te)):
                    continue
                ts_m, te_m = hhmm_to_min(ts), hhmm_to_min(te)
                total += (te_m - ts_m) if te_m > ts_m else (te_m - ts_m + 1440)
            return total

        if is_type2:
            if ceiling_mode == "contiguous":
                # Sum check: trip total across all days vs ceiling
                trip_total = sum(_day_engagement(d) for d in days)
                if trip_total > ceiling:
                    _ded("F2e", 0.30,
                         f"type2 contiguous time-ceiling overrun: "
                         f"{trip_total:.0f}min engaged (ceiling {ceiling}min)")
                # Window check (P6-T4b): when start_time is provided, every
                # activity must fall within [start, start+ceiling]. Per
                # activity outside → −0.15.
                if isinstance(start_time, str) and VALID_TIME_RE.match(start_time):
                    win_start = hhmm_to_min(start_time)
                    win_end   = win_start + int(ceiling)
                    for _di, _d in enumerate(days):
                        for a in _d.get("activities", []):
                            if a.get("activity_type") == "transport":
                                continue
                            ts = a.get("time_start", "")
                            te = a.get("time_end", "")
                            if not (VALID_TIME_RE.match(ts) and VALID_TIME_RE.match(te)):
                                continue
                            ts_m = hhmm_to_min(ts)
                            te_m = hhmm_to_min(te)
                            if te_m < ts_m:   # overnight — normalise
                                te_m += 1440
                            if ts_m < win_start or te_m > win_end:
                                vname = venues.get(a.get("venue_id",""),
                                                    {}).get("name", a.get("venue_id",""))
                                _ded("F2e", 0.15,
                                     f"Day {_di+1}: '{vname}' {ts}-{te} outside "
                                     f"contiguous window "
                                     f"[{start_time}, +{int(ceiling)}min]")
            else:  # ceiling_mode == "spread"
                if ceiling_scope == "per_day":
                    for _di, _d in enumerate(days):
                        _day_total = _day_engagement(_d)
                        if _day_total > ceiling:
                            _ded("F2e", 0.30,
                                 f"Day {_di+1} per-day time-ceiling overrun "
                                 f"(spread): {_day_total:.0f}min engaged "
                                 f"(ceiling {ceiling}min)")
                else:  # "total" (default)
                    trip_total = sum(_day_engagement(d) for d in days)
                    if trip_total > ceiling:
                        _ded("F2e", 0.30,
                             f"type2 spread time-ceiling overrun: "
                             f"{trip_total:.0f}min engaged (ceiling {ceiling}min)")
        else:
            # Non-type2 forward-looking branch — per-day check.
            for _di, _d in enumerate(days):
                _day_total = _day_engagement(_d)
                if _day_total > ceiling:
                    _ded("F2e", 0.30,
                         f"Day {_di+1} time-ceiling overrun: "
                         f"{_day_total:.0f}min engaged (ceiling {ceiling}min)")

    # ── Final score ───────────────────────────────────────────────────────────
    total = sum(d["amount"] for d in deductions)
    score = max(0.0, round(1.0 - total, 3))
    has_critical = any(d["amount"] >= 0.10 for d in deductions)

    return {
        "score":               score,
        "has_critical_issues": has_critical,
        "deductions":          deductions,
    }




# ─────────────────────────────────────────────────────────────────────────────
# TIER 3 HELPERS — LLM Judge
# ─────────────────────────────────────────────────────────────────────────────

LLM_JUDGE_MODEL = "claude-sonnet-4-20250514"


def _activity_lines(plan: dict, venues: dict, include_cost: bool = False) -> str:
    """Shared helper: build the activity summary block for judge prompts."""
    lines = []
    for day in plan.get("days", []):
        dn = day.get("day", "?")
        for act in day.get("activities", []):
            vid   = act.get("venue_id", "")
            vname = act.get("venue_name", vid)
            atype = act.get("activity_type", "")
            ts    = act.get("time_start", "")
            te    = act.get("time_end", "")
            time_str = f" {ts}–{te}" if ts and te else ""
            labels    = venues.get(vid, {}).get("tags", [])
            label_str = ", ".join(labels[:6]) if labels else "none"
            line = f"  Day {dn} [{atype}]{time_str} {vname} (labels: {label_str})"
            if include_cost:
                cost = act.get("estimated_cost_local", 0)
                regs = venues.get(vid, {}).get("regulations", {})
                reg_str = ", ".join(f"{k}={v}" for k, v in regs.items() if v is not None)
                line += f" ${cost}"
                if reg_str:
                    line += f" regs=[{reg_str}]"
            lines.append(line)
    return "\n".join(lines) or "  (no activities)"


def _build_judge_prompt(constraint: dict, plan: dict, task: dict, venues: dict) -> str:
    """Prompt for a single LLM-judged constraint."""
    source_quote = constraint.get("source_in_profile", "")
    description  = constraint.get("description", constraint.get("id", ""))
    hint         = constraint.get("params", {}).get("judge_prompt_hint", "")
    activities   = _activity_lines(plan, venues)
    notes        = plan.get("planning_notes", "") or "(none)"

    return f"""You are a travel plan evaluator. Score a single personalisation constraint.

## User preference
"{source_quote}"

## Constraint
{description}

## Guidance
{hint}

## Plan activities (with times and venue labels)
{activities}

## Agent planning notes
{notes}

Respond ONLY with a JSON object (no markdown):
{{"score": <0.0 | 0.5 | 1.0>, "reasoning": "<1-2 sentences>"}}

1.0 = clearly satisfied  |  0.5 = partial or ambiguous  |  0.0 = clearly not satisfied"""


def _build_batch_judge_prompt(constraints: list, plan: dict,
                               task: dict, venues: dict) -> str:
    """Single prompt for all semantic constraints (hybrid method)."""
    activities = _activity_lines(plan, venues)
    notes      = plan.get("planning_notes", "(none)")

    cblock = ""
    for i, c in enumerate(constraints, 1):
        cid  = c.get("id", "")
        desc = c.get("description", cid)
        quot = c.get("source_in_profile", "")
        hint = c.get("params", {}).get("judge_prompt_hint", "")
        cblock += f"Constraint {i} (id: {cid})\n"
        cblock += f"  User said: \"{quot}\"\n"
        cblock += f"  Description: {desc}\n"
        if hint:
            cblock += f"  Guidance: {hint}\n"
        cblock += "\n"

    ids_list = ", ".join(f'"{c.get("id")}"' for c in constraints)
    return "\n".join([
        "You are a travel plan evaluator. Score each personalisation constraint independently.",
        "",
        "## Plan activities (with times and venue labels)",
        activities,
        "",
        "## Agent planning notes",
        notes,
        "",
        "## Constraints to score",
        cblock.rstrip(),
        "",
        "Score each constraint independently — do NOT let one influence another.",
        "",
        "Respond ONLY with a JSON array (no markdown):",
        "[",
        '  {"id": <id>, "score": <0.0 | 0.5 | 1.0>, "reasoning": "<1-2 sentences>"},',
        "  ...",
        "]",
        "",
        f"IDs to return (in order): [{ids_list}]",
        "",
        "Scoring: 1.0=clearly satisfied, 0.5=partial/ambiguous, 0.0=clearly not satisfied",
    ])


def _build_full_judge_prompt(constraints: list, plan: dict,
                              task: dict, venues: dict) -> str:
    """Single prompt for ALL constraints (llm_all method)."""
    activities = _activity_lines(plan, venues, include_cost=True)
    notes      = plan.get("planning_notes", "(none)")

    cblock = ""
    for i, c in enumerate(constraints, 1):
        cid     = c.get("id", "")
        desc    = c.get("description", cid)
        pattern = c.get("pattern", c.get("check_method", "?"))
        quot    = c.get("source_in_profile", "")
        params  = c.get("params", {})
        hint    = params.get("judge_prompt_hint", "")
        psummary = ", ".join(f"{k}={v}" for k, v in params.items() if k != "judge_prompt_hint")
        cblock += f"Constraint {i} (id: {cid}, pattern: {pattern})\n"
        cblock += f"  User said: \"{quot}\"\n"
        cblock += f"  Description: {desc}\n"
        if psummary: cblock += f"  Params: {psummary}\n"
        if hint:     cblock += f"  Guidance: {hint}\n"
        cblock += "\n"

    ids_list = ", ".join(f'"{c.get("id")}"' for c in constraints)
    return "\n".join([
        "You are a travel plan evaluator. Score every personalisation constraint.",
        "For code-style constraints apply the rule exactly.",
        "For semantic constraints use your judgment. Score each independently.",
        "",
        "## Plan activities (with times, labels, regulations)",
        activities,
        "",
        "## Agent planning notes",
        notes,
        "",
        "## All constraints to score",
        cblock.rstrip(),
        "",
        "Respond ONLY with a JSON array (no markdown):",
        "[",
        '  {"id": <id>, "score": <0.0 | 0.5 | 1.0>, "reasoning": "<1-2 sentences>"},',
        "  ...",
        "]",
        "",
        f"IDs to return (in order): [{ids_list}]",
        "",
        "Scoring: 1.0=clearly satisfied/rule met, 0.5=partial/borderline, 0.0=not satisfied/violated",
    ])


def _llm_client(api_key: str):
    if not HAS_ANTHROPIC:
        raise ImportError("anthropic package not installed — run: pip install anthropic")
    return _anthropic.Anthropic(api_key=api_key)


def _parse_llm_json(raw: str) -> object:
    return json.loads(_strip_fences(raw))


def _call_llm_judge(prompt: str, api_key: str) -> tuple[float, str]:
    """Single-constraint judge. Returns (score, reasoning)."""
    try:
        client = _llm_client(api_key)
        resp   = client.messages.create(
            model=LLM_JUDGE_MODEL, max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        parsed = _parse_llm_json(resp.content[0].text)
        score  = max(0.0, min(1.0, float(parsed.get("score", 0.0))))
        return score, parsed.get("reasoning", "")
    except Exception as e:
        return 0.0, f"LLM judge error: {e}"


def _parse_batch_response(raw: str, constraints: list) -> list[dict]:
    """Parse a batch judge JSON array, returning one entry per constraint."""
    parsed     = _parse_llm_json(raw)
    result_map = {r["id"]: r for r in parsed}
    results    = []
    for c in constraints:
        cid   = c.get("id", "")
        entry = result_map.get(cid)
        if entry:
            score = max(0.0, min(1.0, float(entry.get("score", 0.0))))
            results.append({"id": cid, "score": score, "reasoning": entry.get("reasoning", "")})
        else:
            results.append({"id": cid, "score": 0.0,
                             "reasoning": "Missing from batch response — scored 0"})
    return results


def _call_llm_judge_batch(constraints: list, plan: dict,
                           task: dict, venues: dict, api_key: str) -> list[dict]:
    """All semantic constraints in one API call. Falls back to per-constraint on parse error."""
    if not constraints:
        return []
    prompt = _build_batch_judge_prompt(constraints, plan, task, venues)
    try:
        client = _llm_client(api_key)
        resp   = client.messages.create(
            model=LLM_JUDGE_MODEL, max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        return _parse_batch_response(resp.content[0].text, constraints)
    except Exception:
        # Fallback: score each constraint individually
        results = []
        for c in constraints:
            cid = c.get("id", "")
            p   = _build_judge_prompt(c, plan, task, venues)
            score, reasoning = _call_llm_judge(p, api_key)
            results.append({"id": cid, "score": score, "reasoning": f"[fallback] {reasoning}"})
        return results


def _call_llm_judge_full(constraints: list, plan: dict,
                          task: dict, venues: dict, api_key: str) -> list[dict]:
    """All constraints (code + semantic) in one API call (llm_all method)."""
    if not constraints:
        return []
    prompt = _build_full_judge_prompt(constraints, plan, task, venues)
    try:
        client = _llm_client(api_key)
        resp   = client.messages.create(
            model=LLM_JUDGE_MODEL, max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        return _parse_batch_response(resp.content[0].text, constraints)
    except Exception as e:
        return [{"id": c.get("id",""), "score": 0.0, "reasoning": f"Full judge error: {e}"}
                for c in constraints]


# ─────────────────────────────────────────────────────────────────────────────
# TIER 3: P-SCORE — Pattern handler registry
# To add a new pattern: write a handler and register it in P_SCORE_HANDLERS.
# ─────────────────────────────────────────────────────────────────────────────

def _activities_of_type(activities: list, activity_type: str) -> list:
    if activity_type == "any":
        return activities
    return [a for a in activities if a.get("activity_type") == activity_type]




def _handle_weather_aware(cid, params, days, all_activities, venues):
    """
    Agent chose weather-appropriate venues given the forecast.

    params:
      max_condition: worst acceptable condition ("cloudy", "rainy" etc.)
                     Activities at outdoor venues worse than this score 0.
      activity_type: "any" | specific type (default "any")

    Reads weather_forecast.json; if absent, returns 0.5 (cannot determine).
    """
    max_cond    = params.get("max_condition", "cloudy")
    atype       = params.get("activity_type", "any")
    # city injected by evaluate_p_score via params at call time or default paris
    city        = params.get("city", params.get("_city", "paris"))
    targets     = _activities_of_type(all_activities, atype)

    if not targets:
        return {"id": cid, "score": 1.0, "reason": "No applicable activities to check"}

    try:
        forecast_data = load_weather_forecast(city)   # already unwrapped {date_str: {...}}
    except Exception:
        return {"id": cid, "score": 0.5, "reason": "Weather forecast unavailable — partial credit"}

    max_sev   = _WEATHER_SEV.get(max_cond, 2)
    per_act   = []
    for day in days:
        date_str = day.get("date", "")
        wx       = forecast_data.get(date_str, {})
        wx_cond  = wx.get("condition", "sunny")
        wx_sev   = _WEATHER_SEV.get(wx_cond, 0)

        for act in day.get("activities", []):
            if atype != "any" and act.get("activity_type") != atype:
                continue
            vid   = act.get("venue_id", "")
            vname = act.get("venue_name", vid)
            sens  = venues.get(vid, {}).get("outdoor_sensitivity", "none")

            if not date_str or not wx or sens == "none":
                per_act.append((1.0, f"{vname}: indoor/no weather impact ✓"))
                continue

            if wx_sev <= max_sev:
                per_act.append((1.0, f"{vname}: weather={wx_cond} within acceptable range ✓"))
            else:
                covered = venues.get(vid, {}).get("covered", False)
                if covered and wx_sev < 4:
                    per_act.append((0.5, f"{vname}: {sens} (covered) on {wx_cond} — acceptable but not ideal"))
                else:
                    per_act.append((0.0, f"{vname}: {sens} on {wx_cond} — violates weather_aware constraint"))

    if not per_act:
        return {"id": cid, "score": 1.0, "reason": "No outdoor activities found"}

    score = sum(s for s, _ in per_act) / len(per_act)
    return {"id": cid, "score": round(score, 3),
            "reason": "; ".join(msg for _, msg in per_act)}


    """
    Agent chose weather-appropriate venues given the forecast.

    params:
      max_condition: worst acceptable condition ("cloudy", "rainy" etc.)
                     Activities at outdoor venues worse than this score 0.
      activity_type: "any" | specific type (default "any")

    Requires weather_forecast data embedded in venue context. Since the
    evaluator doesn't re-call the tool, we check outdoor_sensitivity against
    the task's date and the global weather_forecast.json.

    The check is applied per-activity: outdoor-sensitive venues on bad-weather
    days are penalised proportionally to severity.
    """
    max_cond    = params.get("max_condition", "cloudy")
    atype       = params.get("activity_type", "any")
    city        = params.get("city", "paris")           # set by evaluate_p_score
    targets     = _activities_of_type(all_activities, atype)

    if not targets:
        return {"id": cid, "score": 1.0, "reason": "No applicable activities to check"}

    # Load weather forecast
    try:
        forecast_data = json.loads(
            (DATA_DIR / "ground_truth" / city / "weather_forecast.json").read_text()
        ).get(city, {})
    except Exception:
        return {"id": cid, "score": 0.5, "reason": "Weather forecast unavailable — partial credit"}

    max_sev   = _WEATHER_SEV.get(max_cond, 2)
    per_act   = []
    for day in days:
        date_str = day.get("date", "")
        wx       = forecast_data.get(date_str, {})
        wx_cond  = wx.get("condition", "sunny")
        wx_sev   = _WEATHER_SEV.get(wx_cond, 0)

        for act in day.get("activities", []):
            if atype != "any" and act.get("activity_type") != atype:
                continue
            vid   = act.get("venue_id", "")
            vname = act.get("venue_name", vid)
            sens  = venues.get(vid, {}).get("outdoor_sensitivity", "none")

            if not date_str or not wx or sens == "none":
                per_act.append((1.0, f"{vname}: indoor/no weather impact ✓"))
                continue

            if wx_sev <= max_sev:
                per_act.append((1.0, f"{vname}: weather={wx_cond} within acceptable range ✓"))
            else:
                # Outdoor venue on bad day
                covered = venues.get(vid, {}).get("covered", False)
                if covered and wx_sev < 4:
                    per_act.append((0.5, f"{vname}: {sens} (covered) on {wx_cond} — acceptable but not ideal"))
                else:
                    per_act.append((0.0, f"{vname}: {sens} on {wx_cond} — violates weather_aware constraint"))

    if not per_act:
        return {"id": cid, "score": 1.0, "reason": "No outdoor activities found"}

    score = sum(s for s, _ in per_act) / len(per_act)
    return {"id": cid, "score": round(score, 3),
            "reason": "; ".join(msg for _, msg in per_act)}


# ─────────────────────────────────────────────────────────────────────────────
# SPRINT 4 CONSTRAINT HANDLERS  (evaluator-only, no new tools)
# ─────────────────────────────────────────────────────────────────────────────

NOISE_ORDER = ["quiet", "moderate", "loud"]


def _handle_opening_time_required(cid, params, days, all_activities, venues):
    """
    Venue must be open before open_before (HH:MM) — i.e. first window opens ≤ threshold.
    Useful for early-bird sightseeing: 'I always start before 10am'.
    """
    open_before = params.get("open_before", "10:00")
    atype       = params.get("activity_type", "visit")
    threshold   = hhmm_to_min(open_before)

    per_venue = []
    for day_idx, day in enumerate(days):
        _, day_key = get_day_date({"start_date": "2000-01-01", "days": 99}, day_idx)
        # We need the actual task — use day["day_of_week"] if available
        day_key_actual = day.get("day_of_week", day_key)[:3].lower()
        for a in day.get("activities", []):
            if atype != "any" and a.get("activity_type") != atype:
                continue
            vid   = a.get("venue_id", "")
            vname = a.get("venue_name", vid)
            hours = venues.get(vid, {}).get("hours", {}).get(day_key_actual)
            if hours is None:
                per_venue.append((0.5, f"{vname}: closed on {day_key_actual} — cannot verify"))
                continue
            # hours may be ["HH:MM-HH:MM"] (combined) or ["HH:MM", "HH:MM"] (split)
            first_window = hours[0] if hours else "00:00"
            first_open_str = first_window.split("-")[0] if "-" in first_window else first_window
            first_open = hhmm_to_min(first_open_str) if first_open_str else 0
            if first_open <= threshold:
                per_venue.append((1.0, f"{vname}: opens {first_open_str} ≤ {open_before} ✓"))
            else:
                per_venue.append((0.0, f"{vname}: opens {first_open_str} > {open_before} (too late)"))

    if not per_venue:
        return {"id": cid, "score": 1.0, "reason": "No applicable activities"}
    score = sum(s for s, _ in per_venue) / len(per_venue)
    return {"id": cid, "score": round(score, 3),
            "reason": "; ".join(msg for _, msg in per_venue)}


# NOTE: _handle_temporal_cross_day was migrated to the generic constraint engine
# (scope:per_day + condition:{field==value} + aggregation:{at_most:N}).
# See ENGINE_MIGRATION_TODO.md Phase 4 pre-work.

def _handle_dependency_chain(cid, params, days, all_activities, venues):
    """
    A venue that cannot supply food/service requires a supporting activity
    nearby at the correct timing.

    Rubric params:
      anchor_venue_id:   the venue that has the dependency (e.g. par_s08)
      requirement:       "meal_within_walk"    (only type for now)
      max_walk_minutes:  int — supporting activity must be ≤ this walk away
      timing:            "before" | "after" | "either"

    Scoring:
      1.0  — supporting activity present, correct timing, within walk distance
      0.5  — supporting activity present but wrong timing or distance slightly over
      0.0  — no supporting activity found near anchor, or anchor absent from plan

    If anchor not in plan → skip (not applicable), return 1.0 with note.
    """
    anchor_id    = params.get("anchor_venue_id", "")
    requirement  = params.get("requirement", "meal_within_walk")
    max_walk     = int(params.get("max_walk_minutes", 15))
    timing       = params.get("timing", "before")   # "before" | "after" | "either"
    city         = params.get("city", "paris")

    # Load travel matrix for distance checks
    try:
        _, matrix, _ = load_ground_truth(city)
        if not matrix:
            return {"id": cid, "score": 0.5, "reason": "Travel matrix unavailable — partial credit"}
    except Exception:
        return {"id": cid, "score": 0.5, "reason": "Travel matrix unavailable — partial credit"}

    # Is anchor in the plan?
    anchor_acts = [a for a in all_activities if a.get("venue_id") == anchor_id]
    if not anchor_acts:
        return {"id": cid, "score": 1.0,
                "reason": f"Anchor {anchor_id} not in plan — constraint not applicable"}

    anchor_name = venues.get(anchor_id, {}).get("name", anchor_id)

    # For each anchor occurrence, find a supporting activity
    per_anchor = []
    for anchor_act in anchor_acts:
        anchor_start = hhmm_to_min(anchor_act.get("time_start", "00:00"))
        anchor_end   = hhmm_to_min(anchor_act.get("time_end",   "00:00"))
        anchor_day   = next((d["day"] for d in days
                             if anchor_act in d.get("activities", [])), None)

        # Find candidate supporting activities (same day, correct type)
        if requirement == "meal_within_walk":
            candidates = [a for a in all_activities
                          if a.get("activity_type") == "meal"
                          and a.get("venue_id") != anchor_id
                          and next((d["day"] for d in days
                                    if a in d.get("activities", [])), None) == anchor_day]
        else:
            candidates = []

        if not candidates:
            per_anchor.append((0.0, f"{anchor_name}: no meal activity on same day"))
            continue

        # Check timing + walk distance
        best_score = 0.0
        best_msg   = f"{anchor_name}: no suitable nearby meal found"

        for cand in candidates:
            cand_vid  = cand.get("venue_id", "")
            cand_name = venues.get(cand_vid, {}).get("name", cand_vid)
            cand_end  = hhmm_to_min(cand.get("time_end",   "00:00"))
            cand_start= hhmm_to_min(cand.get("time_start", "00:00"))

            # Walk distance check (bidirectional)
            k1 = f"{anchor_id}_to_{cand_vid}"
            k2 = f"{cand_vid}_to_{anchor_id}"
            walk = matrix.get(k1) or matrix.get(k2)
            if walk is None:
                continue

            # Timing check
            if timing == "before":
                # Meal must end before anchor starts (with walk buffer)
                timing_ok = cand_end + walk <= anchor_start
            elif timing == "after":
                # Meal must start after anchor ends (with walk buffer)
                timing_ok = cand_start >= anchor_end + walk
            else:  # "either"
                timing_ok = (cand_end + walk <= anchor_start or
                             cand_start >= anchor_end + walk)

            within_walk = walk <= max_walk

            if timing_ok and within_walk:
                score = 1.0
                msg   = (f"{anchor_name}: meal at {cand_name} is {walk}min walk, "
                         f"correctly timed {timing} visit ✓")
            elif within_walk and not timing_ok:
                score = 0.5
                msg   = (f"{anchor_name}: {cand_name} nearby ({walk}min) "
                         f"but timing wrong (expected {timing})")
            elif timing_ok and not within_walk:
                score = 0.5
                msg   = (f"{anchor_name}: {cand_name} timed correctly but "
                         f"{walk}min walk exceeds {max_walk}min limit")
            else:
                score = 0.0
                msg   = (f"{anchor_name}: {cand_name} — wrong timing "
                         f"and {walk}min walk exceeds {max_walk}min")

            if score > best_score:
                best_score = score
                best_msg   = msg

        per_anchor.append((best_score, best_msg))

    score = sum(s for s, _ in per_anchor) / len(per_anchor) if per_anchor else 1.0
    return {"id": cid, "score": round(score, 3),
            "reason": "; ".join(msg for _, msg in per_anchor)}



# ─────────────────────────────────────────────────────────────────────────────
# SPRINT B2 — GENERIC CONSTRAINT SCHEMA ENGINE + NEW HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

# Engine extracted to shared module — import here for backwards compatibility
from scripts.generation.constraint_engine import (
    TRAFFIC_TIER_ORDER,
    PRICE_TIER_ORDER,
    PACE_ORDER,
    _activity_matches_scope,
    _activity_satisfies_condition,
    _evaluate_generic_constraint,
)


def _get_venue_field(vid: str, field: str, venues: dict):
    """Get a field from venue ground truth."""
    v = venues.get(vid, {})
    return v.get(field)


# ── Consecutive-pair check ────────────────────────────────────────────────────

def _handle_consecutive_pairs(cid: str, params: dict,
                               days: list, all_activities: list,
                               venues: dict) -> dict:
    """
    Check properties of adjacent activity pairs within each day.

    params:
      check_type: "alternates" | "no_consecutive"
      field:      venue field to check
      values:     for "alternates": list of 2 values that should alternate
                  for "no_consecutive": value that should not appear twice in a row
      threshold:  fraction of pairs that must pass (default 0.5 for partial credit)
    """
    check_type = params.get("check_type", "no_consecutive")
    field      = params.get("field", "recommended_pace")
    values     = params.get("values", [])
    threshold  = params.get("threshold", 0.5)

    total_pairs   = 0
    passing_pairs = 0

    for day in days:
        acts = day.get("activities", [])
        for i in range(len(acts) - 1):
            a1, a2 = acts[i], acts[i+1]
            v1 = venues.get(a1.get("venue_id",""), {}).get(field)
            v2 = venues.get(a2.get("venue_id",""), {}).get(field)
            total_pairs += 1

            if check_type == "no_consecutive":
                bad_val = values[0] if values else None
                if v1 != bad_val or v2 != bad_val:
                    passing_pairs += 1
            elif check_type == "alternates":
                # Check they differ (either ordering)
                if v1 != v2:
                    passing_pairs += 1

    if total_pairs == 0:
        return {"id": cid, "score": 1.0, "reason": "No consecutive pairs to check"}

    ratio = passing_pairs / total_pairs
    score = 1.0 if ratio >= threshold else (0.5 if ratio > 0 else 0.0)
    return {"id": cid, "score": score,
            "reason": f"consecutive_pairs: {passing_pairs}/{total_pairs} pairs pass ({ratio:.0%})"}


# ── Handlers migrated to generic engine schema ───────────────────────────────
# local_cuisine_preference  → scope:activity_type=meal + condition:{local_cuisine==1}
#                             + aggregation:{ratio:N}
# cuisine_diversity_minimum → scope:activity_type=meal + condition:{}
#                             + aggregation:{count_distinct:N, field:cuisine_label}
# temporal_cross_day        → scope:per_day or all + condition:{field==value}
#                             + aggregation:{at_most:N} or {at_most_distinct:N}
# All 21 Bucket A handlers + 3 misclassified Bucket C handlers deleted.
# See docs/ENGINE_MIGRATION_TODO.md for full migration record.


P_SCORE_HANDLERS = {
    # Bucket C — patterns genuinely irreducible to the generic engine schema.
    # Irreducible because they require: external state (weather forecast),
    # day-keyed nested venue data (opening hours), cross-activity relational
    # logic + travel matrix (dependency), or adjacent-pair ordering (consecutive).
    "weather_aware":          _handle_weather_aware,
    "dependency_chain":       _handle_dependency_chain,
    "consecutive_pairs":      _handle_consecutive_pairs,
    "opening_time_required":  _handle_opening_time_required,
}


def evaluate_p_score(result: dict, task: dict, venues: dict,
                     api_key: Optional[str] = None,
                     judge_method: str = "hybrid") -> dict:
    """
    judge_method="hybrid" (default):
        Code constraints → deterministic handlers (+ source_required post-processing).
        Semantic constraints → single batched LLM call.

    judge_method="llm_all":
        All constraints → single LLM call.
        source_required not applied in this mode (LLM has no tool log visibility).
    """
    plan                 = result.get("parsed_plan")
    personal_constraints = task.get("rubric", {}).get("personal_constraints", [])

    if plan is None or not personal_constraints:
        return {"score": None, "note": "No plan or no personal constraints",
                "code_results": [], "llm_results": [], "llm_pending": [],
                "judge_method": judge_method}

    days           = plan.get("days", [])
    all_activities = []
    tool_log       = result.get("tool_call_log", [])
    city           = task.get("city", "paris")

    # Enrich each day with its ISO date string for weather_aware checks
    # Also tag each activity with _day_date for date-based time_window scopes
    # Transport activities are excluded from P-score evaluation — constraints
    # are about venue choices, not transport.
    _enriched_days = []
    for day_idx, day in enumerate(days):
        date_str, _ = get_day_date(task, day_idx)
        _enriched_day = {**day, "date": date_str}
        for act in day.get("activities", []):
            act["_day_date"] = date_str
            if act.get("activity_type") != "transport":
                all_activities.append(act)
        _enriched_days.append(_enriched_day)
    days = _enriched_days

    if judge_method == "llm_all":
        if not api_key:
            return {"score": None, "code_results": [], "llm_results": [],
                    "llm_pending": [c.get("id") for c in personal_constraints],
                    "judge_method": judge_method,
                    "note": f"llm_all: {len(personal_constraints)} constraints pending (no api_key)"}
        llm_results = _call_llm_judge_full(personal_constraints, plan, task, venues, api_key)
        scores      = [r["score"] for r in llm_results]
        return {"score":        round(sum(scores)/len(scores), 3) if scores else None,
                "code_results": [], "llm_results": llm_results, "llm_pending": [],
                "judge_method": judge_method,
                "note": f"llm_all: {len(llm_results)} constraints in 1 call"}

    # Hybrid: split by check_method
    code_results = []
    semantic_cs  = []

    for c in personal_constraints:
        cid        = c.get("id", "")
        is_llm     = c.get("check_method") == "llm" or c.get("pattern") == "semantic"

        if is_llm:
            semantic_cs.append(c)
            continue

        # Dispatch: generic-schema constraints go to the engine directly.
        # Bucket C pattern-based constraints go to legacy handlers.
        # Anything else is an error.
        if "scope" in c:
            try:
                constraint_for_engine = {
                    "id":          cid,
                    "scope":       c["scope"],
                    "condition":   c.get("condition", {}),
                    "aggregation": c.get("aggregation", "all"),
                    "description": c.get("description", cid),
                }
                from scripts.generation.constraint_engine import _evaluate_generic_constraint
                raw = _evaluate_generic_constraint(
                    cid, constraint_for_engine, days, all_activities, venues
                )
                raw = _apply_source_required(raw, c, tool_log, city)
                code_results.append(raw)
            except Exception as e:
                code_results.append({"id": cid, "score": 0.0,
                                      "reason": f"Engine error: {e}"})
            continue

        pattern = c.get("pattern", "")
        params  = c.get("params", {})
        handler = P_SCORE_HANDLERS.get(pattern)
        if handler is None:
            code_results.append({"id": cid, "score": 0.0,
                                  "reason": f"Unknown pattern '{pattern}' — not in Bucket C handlers and no generic schema"})
            continue
        try:
            params_with_city = {**params, "city": city}
            raw = handler(cid, params_with_city, days, all_activities, venues)
            raw = _apply_source_required(raw, c, tool_log, city)
            code_results.append(raw)
        except Exception as e:
            code_results.append({"id": cid, "score": 0.0,
                                  "reason": f"Handler error for '{pattern}': {e}"})

    if semantic_cs and api_key:
        llm_results = _call_llm_judge_batch(semantic_cs, plan, task, venues, api_key)
        llm_pending = []
    elif semantic_cs:
        llm_results = []
        llm_pending = [c.get("id") for c in semantic_cs]
    else:
        llm_results = []
        llm_pending = []

    all_scored = [r["score"] for r in code_results] + [r["score"] for r in llm_results]
    final      = round(sum(all_scored)/len(all_scored), 3) if all_scored else None

    note_parts = ["method=hybrid"]
    if code_results: note_parts.append(f"{len(code_results)} code")
    if llm_results:  note_parts.append(f"{len(llm_results)} semantic in 1 LLM call")
    if llm_pending:  note_parts.append(f"{len(llm_pending)} semantic pending (no api_key)")

    return {"score": final, "code_results": code_results,
            "llm_results": llm_results, "llm_pending": llm_pending,
            "judge_method": judge_method, "note": "; ".join(note_parts)}


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE SCORE
# ─────────────────────────────────────────────────────────────────────────────

# Scoring formula (Sprint B design):
#   if any C issue:         final_score = 0  (C is a hard gate)
#   F hard fails reduce F-score proportionally (no gate)
#   else: final_score = 0.7 × F_partial + 0.3 × P + 0.1 × B
#   Theoretical maximum: 1.2 (B-score is bonus, clearly marked)
B_WEIGHT   = 0.1   # additive bonus — not normalised into base

DEFAULT_C_THRESHOLD = 0.0
DEFAULT_F_THRESHOLD = 0.0


def _skipped(tier: str, reason: str) -> dict:
    return {"score": None, "skipped": True, "skip_reason": reason, "tier": tier}




# ─────────────────────────────────────────────────────────────────────────────
# SPRINT B3 — B-SCORE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _get_walk_minutes(va: str, vb: str, matrix: dict) -> Optional[float]:
    """Get walking minutes between two venues from matrix. Tries both orderings.
    Handles both dict format {"walking": N, "transit": N} (from load_ground_truth)
    and flat integer format (from test fixtures)."""
    entry = matrix.get(f"{va}_to_{vb}") or matrix.get(f"{vb}_to_{va}")
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("walking")
    return entry


def _compute_mst_minutes(venue_ids: list, matrix: dict) -> float:
    """
    Compute Minimum Spanning Tree total edge weight using Prim's algorithm.
    Edge weight = walk_minutes between venue pairs.
    Returns total MST minutes (theoretical minimum travel for any route visiting all venues).
    """
    if len(venue_ids) <= 1:
        return 0.0

    vids = list(venue_ids)
    in_tree  = {vids[0]}
    total    = 0.0

    while len(in_tree) < len(vids):
        best_cost = float("inf")
        best_node = None
        for v in in_tree:
            for u in vids:
                if u in in_tree:
                    continue
                cost = _get_walk_minutes(v, u, matrix)
                if cost is not None and cost < best_cost:
                    best_cost = cost
                    best_node = u
        if best_node is None:
            # Some venues not in matrix — estimate 15min as default
            for u in vids:
                if u not in in_tree:
                    best_node = u
                    best_cost = 15.0
                    break
        in_tree.add(best_node)
        total += best_cost

    return total


def _check_route_efficiency(plan: dict, matrix: dict,
                              threshold: float = 1.5) -> dict:
    """
    For each day: compute actual travel (sum of consecutive pair walk times)
    vs MST minimum. Score 1.0 if actual ≤ threshold × MST, else 0.0.
    Returns per-day results and overall score.
    """
    day_results = []
    for day_idx, day in enumerate(plan.get("days", [])):
        acts = day.get("activities", [])
        vids = [a.get("venue_id","") for a in acts if a.get("venue_id")]

        if len(vids) <= 1:
            day_results.append({"day": day_idx+1, "score": 1.0,
                                 "note": "≤1 venue, no routing to evaluate"})
            continue

        # Actual travel: sum of consecutive pair times
        actual = 0.0
        for i in range(len(vids) - 1):
            t = _get_walk_minutes(vids[i], vids[i+1], matrix)
            actual += t if t is not None else 15.0  # 15min default if missing

        # MST on unique venues (theoretical min for any route visiting all distinct venues)
        unique_vids = list(dict.fromkeys(vids))
        mst = _compute_mst_minutes(unique_vids, matrix)

        if mst == 0:
            day_results.append({"day": day_idx+1, "score": 1.0,
                                 "note": "MST=0, all venues co-located"})
            continue

        efficiency = mst / actual if actual > 0 else 1.0
        passed     = actual <= threshold * mst
        day_results.append({
            "day":        day_idx + 1,
            "score":      1.0 if passed else 0.0,
            "actual_min": round(actual, 1),
            "mst_min":    round(mst, 1),
            "efficiency": round(efficiency, 3),
            "note":       f"actual={actual:.0f}min, MST={mst:.0f}min, ratio={actual/mst:.2f}x"
                          + (" ✓" if passed else f" ✗ (threshold {threshold}x)"),
        })

    if not day_results:
        return {"score": None, "day_results": [], "note": "No days to evaluate"}

    avg = sum(d["score"] for d in day_results) / len(day_results)
    return {"score": round(avg, 3), "day_results": day_results,
            "note": f"Route efficiency: {avg:.0%} days pass ({threshold}x MST threshold)"}


def _check_doc_appeared(venue_id: str, tool_log: list,
                          date: Optional[str] = None) -> bool:
    """
    Check if the agent called get_official_site for this venue,
    optionally with a specific date. Used for B-score awareness checks.
    """
    for call in tool_log:
        if call.get("tool_name") != "get_official_site":
            continue
        inp = call.get("tool_input", {})
        if inp.get("venue_id") != venue_id:
            continue
        if date is not None and inp.get("date") != date:
            continue
        return True
    return False


def _check_blog_mentioned(venue_id: str, venue_name: str, tool_log: list) -> bool:
    """Check if agent searched blogs/forums for this venue."""
    name_lower = (venue_name or "").lower().split()[0] if venue_name else ""
    for call in tool_log:
        if call.get("tool_name") != "search_blogs_and_forums":
            continue
        inp = call.get("tool_input", {})
        q   = (inp.get("query","") + " " + inp.get("venue_name","")).lower()
        if venue_id.lower() in q or (name_lower and name_lower in q):
            return True
    return False


def _run_b_script(script_code: str, plan: dict, task: dict,
                   venues: dict) -> float:
    """
    Run a task-specific Python B-score evaluation script in a sandbox.
    Script must define evaluate(plan, task, venues) -> float.
    Returns score 0.0-1.0, or 0.0 on error.
    """
    import tempfile, subprocess, sys

    wrapper = f"""
{script_code}

import json, sys
plan  = json.loads(sys.argv[1])
task  = json.loads(sys.argv[2])
venues = json.loads(sys.argv[3])
try:
    score = evaluate(plan, task, venues)
    print(float(score))
except Exception as e:
    print(0.0)
"""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                          delete=False) as f:
            f.write(wrapper)
            fpath = f.name

        result = subprocess.run(
            [sys.executable, fpath,
             json.dumps(plan), json.dumps(task), json.dumps(venues)],
            capture_output=True, text=True, timeout=5
        )
        Path(fpath).unlink(missing_ok=True)

        if result.returncode == 0 and result.stdout.strip():
            val = float(result.stdout.strip())
            return max(0.0, min(1.0, val))
        return 0.0
    except Exception:
        return 0.0


def evaluate_b_score(result: dict, task: dict, venues: dict,
                      matrix: dict,
                      api_key: Optional[str] = None) -> dict:
    """
    Evaluate B-score bonus constraints.

    Components:
      1. Route efficiency — MST-based geographic coherence
      2. Doc-appearance awareness — did agent consult key sources
      3. Task-specific Python scripts — bespoke B-score logic
      4. Hop-3 LLM judge — qualitative schedule arc, rest window, narrative

    Returns score 0.0-1.0 (pre-weighting by B_WEIGHT in composite).
    """
    plan          = result.get("parsed_plan")
    tool_log      = result.get("tool_call_log", [])
    b_constraints = task.get("rubric", {}).get("b_score_constraints", [])

    if plan is None:
        return {"score": None, "note": "No plan to evaluate",
                "components": {}, "constraint_results": []}

    components = {}

    # ── 1. Route efficiency ───────────────────────────────────────────────────
    route = _check_route_efficiency(plan, matrix)
    components["route_efficiency"] = route

    # ── 2. B-score constraints ────────────────────────────────────────────────
    constraint_results = []
    llm_pending        = []

    days           = plan.get("days", [])
    all_activities = [a for d in days for a in d.get("activities", [])]

    for c in b_constraints:
        cid     = c.get("id","")
        pattern = c.get("pattern","")
        params  = c.get("params", {})
        desc    = c.get("description","")

        # Doc-appearance check
        if pattern == "doc_appeared":
            vid  = params.get("venue_id")
            date = params.get("date")
            appeared = _check_doc_appeared(vid, tool_log, date)
            constraint_results.append({
                "id": cid, "score": 1.0 if appeared else 0.0,
                "reason": f"doc_appeared({vid}): {'yes' if appeared else 'no'}"
            })

        # Python script runner
        elif pattern == "python_script":
            code  = c.get("script_code","")
            score = _run_b_script(code, plan, task, venues) if code else 0.0
            constraint_results.append({
                "id": cid, "score": score,
                "reason": f"python_script: score={score:.2f}"
            })

        # Consecutive-pair check (reuse B2 handler)
        elif pattern == "consecutive_pairs":
            r = _handle_consecutive_pairs(cid, params, days, all_activities, venues)
            constraint_results.append(r)

        # Generic schema constraint
        elif pattern == "generic":
            r = _evaluate_generic_constraint(cid, c, days, all_activities, venues)
            constraint_results.append(r)

        # LLM semantic judge
        elif pattern == "llm_semantic":
            if api_key:
                llm_pending.append(c)
            else:
                constraint_results.append({
                    "id": cid, "score": None,
                    "reason": "llm_semantic: no api_key provided — skipped"
                })

        else:
            constraint_results.append({
                "id": cid, "score": None,
                "reason": f"Unknown B-score pattern '{pattern}'"
            })

    # ── 3. LLM judge for semantic constraints ─────────────────────────────────
    if llm_pending and api_key:
        for c in llm_pending:
            rubric_prompt = c.get("rubric_prompt", c.get("description",""))
            scoring_guide = c.get("scoring_guide","0.0=fail, 0.5=partial, 1.0=pass")
            try:
                score = _call_b_llm_judge(
                    plan, task, rubric_prompt, scoring_guide, api_key
                )
                constraint_results.append({
                    "id": c.get("id",""), "score": score,
                    "reason": f"llm_semantic: score={score:.2f}"
                })
            except Exception as e:
                constraint_results.append({
                    "id": c.get("id",""), "score": 0.0,
                    "reason": f"llm_semantic error: {e}"
                })

    # ── Aggregate ─────────────────────────────────────────────────────────────
    # Route efficiency is always included (even without explicit b_constraints)
    route_score = route.get("score")

    coded_scores = [r["score"] for r in constraint_results if r.get("score") is not None]

    all_scores = ([route_score] if route_score is not None else []) + coded_scores

    if not all_scores:
        final = None
        note  = "No B-score components evaluated"
    else:
        final = round(sum(all_scores) / len(all_scores), 3)
        note  = (f"B-score: route={route_score:.2f}" if route_score is not None else "B-score: route=N/A") +                 (f", {len(coded_scores)} constraint(s) avg={sum(coded_scores)/len(coded_scores):.2f}"
                 if coded_scores else "")

    return {
        "score":              final,
        "note":               note,
        "components":         components,
        "constraint_results": constraint_results,
        "llm_pending":        [c.get("id") for c in llm_pending] if not api_key else [],
    }


def _call_b_llm_judge(plan: dict, task: dict, rubric_prompt: str,
                       scoring_guide: str, api_key: str) -> float:
    """Call LLM to evaluate a single hop-3 B-score constraint."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url="https://api.anthropic.com/v1")
    prompt = f"""You are evaluating a travel plan for a benchmark.

CONSTRAINT TO CHECK:
{rubric_prompt}

SCORING GUIDE:
{scoring_guide}

TRAVEL PLAN:
{json.dumps(plan, indent=2)}

Respond with ONLY a single number: 0.0, 0.5, or 1.0"""
    try:
        resp = client.chat.completions.create(
            model="claude-sonnet-4-20250514",
            messages=[{"role":"user","content":prompt}],
            max_tokens=10,
        )
        text = resp.choices[0].message.content.strip()
        val  = float(text.split()[0])
        return max(0.0, min(1.0, val))
    except Exception:
        return 0.0

# ─────────────────────────────────────────────────────────────────────────────
# MAIN EVALUATOR
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_SCHEMA_VERSIONS = {"1.0", "1.1"}   # versions this evaluator can handle

def evaluate(result: dict,
             task: Optional[dict] = None,
             db_path: Optional[Path] = None,
             api_key: Optional[str] = None,
             judge_method: str = "hybrid",
             c_threshold: float = DEFAULT_C_THRESHOLD,
             f_threshold: float = DEFAULT_F_THRESHOLD) -> dict:
    task_id = result.get("task_id") or result.get("parsed_plan", {}).get("task_id", "unknown")
    if task is None:
        task = load_task(task_id)
    city           = task["city"].lower()
    task["city"]   = city  # normalize for sub-functions that read task["city"]
    venues, matrix, truth_carriers = load_ground_truth(city, db_path=db_path)

    # Warn if result was produced by an incompatible schema version
    schema_ver = result.get("schema_version", "1.0")
    if schema_ver not in SUPPORTED_SCHEMA_VERSIONS:
        import warnings
        warnings.warn(
            f"Result '{task_id}' has schema_version='{schema_ver}', "
            f"expected one of {SUPPORTED_SCHEMA_VERSIONS}. "
            "Scores may be inaccurate — re-run the agent to get a fresh result.",
            stacklevel=2
        )

    c = evaluate_c_score(result, task, venues)
    c_passed = c.get("passed", True)  # True unless hard gate fired

    # C hard gate: only fires when zero tool calls or no plan produced
    if not c_passed:
        reason = f"C hard gate: {c.get('deductions', [[None,'unknown']])[0][1]}"
        return {"task_id": task_id, "model": result.get("model","unknown"),
                "c_score": c, "f_score": _skipped("f", reason),
                "p_score": _skipped("p", reason),
                "b_score": _skipped("b", reason), "fast_fail": "c_gate"}

    f = evaluate_f_score(result, task, venues, matrix, truth_carriers)

    # Enrich plan activities with travel_minutes before P-score evaluation.
    # This allows the constraint engine to evaluate per-day travel time budgets
    # via {scope: "per_day", aggregation: {sum: "travel_minutes", ...}}.
    # Reuses the same _get_walk_minutes lookup the F-score travel_buffer uses.
    _plan = result.get("parsed_plan")
    if _plan and matrix:
        for _day in _plan.get("days", []):
            _acts = _day.get("activities", [])
            for _i, _act in enumerate(_acts):
                if _i + 1 < len(_acts):
                    _va = _act.get("venue_id", "")
                    _vb = _acts[_i + 1].get("venue_id", "")
                    _tm = _get_walk_minutes(_va, _vb, matrix) if (_va and _vb) else None
                    _act["travel_minutes"] = _tm if _tm is not None else 15.0
                else:
                    _act["travel_minutes"] = 0.0  # last activity — no onward travel

    p = evaluate_p_score(result, task, venues, api_key=api_key, judge_method=judge_method)

    # B-score engine (Sprint B3)
    b = evaluate_b_score(result, task, venues, matrix, api_key=api_key)

    return {"task_id": task_id, "model": result.get("model","unknown"),
            "c_score": c, "f_score": f, "p_score": p, "b_score": b,
            "_task_difficulty": task.get("difficulty", ""),
            }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def print_report(ev: dict):
    """Print a human-readable evaluation report."""
    print(f"\n{'='*60}")
    ff = ev.get("fast_fail")
    print(f"Task:  {ev['task_id']}  |  Model: {ev['model']}"
          + (f"  [fast-fail: {ff}]" if ff else ""))
    print(f"{'='*60}")
    c, f, p = ev["c_score"], ev["f_score"], ev["p_score"]

    print(f"C-Score: {c['score']:.2%}  tools={c.get('tools_used',[])}  "
          f"{'⚠ critical' if not c.get('passed') else ''}")
    for amt, desc in c.get("deductions", []): print(f"  −{amt:.2f} {desc}")
    for w in c.get("warnings", []):           print(f"  ⚠  {w}")

    if f.get("skipped"):
        print(f"\nF-Score: SKIPPED  ({f['skip_reason']})")
    else:
        crit = "⚠ critical issues" if f.get("has_critical_issues") else ""
        print(f"\nF-Score: {f['score']:.2%}  {crit}")
        for d in f.get("deductions", []):
            print(f"  −{d['amount']:.2f} [{d['section']}] {d['reason']}")

    if p.get("skipped"):
        print(f"\nP-Score: SKIPPED  ({p['skip_reason']})")
    else:
        pv = p.get("score")
        pm = p.get("judge_method","")
        print(f"\nP-Score: {pv:.2%}  (method={pm})" if pv is not None
              else "\nP-Score: pending")
        for r in p.get("code_results", []):
            sym = "✓" if r.get("score",0) >= 0.8 else ("~" if r.get("score",0) > 0 else "✗")
            print(f"  {sym} [{r.get('id','?')}] {r.get('reason','')[:80]}")

    b = ev.get("b_score", {})
    bv = b.get("score") if isinstance(b, dict) else None
    if bv is not None:
        print(f"\nB-Score: {bv:.2%}  (bonus)")
