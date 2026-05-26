"""
scripts/generation/doc_agent.py

Phase 1 agent loop for multi-venue document planning.

The agent uses tools to explore the venue pool, reason about geographic
coherence and wrong-info placement, then calls SUBMIT with ~20 doc briefs.
Each brief specifies: doc_type, angle, venue_ids, wrong_info_venues, date.

Phase 2 (generation) uses the briefs but is single-call per type batch —
see generate_multi_venue_docs.py.

Tools:
  HELP(query)                        — dynamic handbook
  THINK(thought)                     — log reasoning steps
  query_pool(filters)                — find venues by category/tag/district/tier
  get_venue(venue_id)                — full detail including coords
  estimate_travel(id_a, id_b, mode)  — haversine distance between two venues
  SUBMIT(briefs)                     — validate + terminate on clean pass

Multi-model: Anthropic, OpenAI (GPT + DeepSeek), Gemini all supported.
Reuses pool utility functions from pool_utils.py.
Owns its own loop, system prompt, and SUBMIT handler.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.pool_utils import (
    _city_centre_from_pool,
    _query_pool,
    _get_venue_detail,
    build_pool_inventory,
)
from scripts.generation.build_travel_matrix import haversine_km, estimate_minutes

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

try:
    import anthropic as _anthropic_mod
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    from google import genai as _google_genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

MAX_TURNS = 15

# ─────────────────────────────────────────────────────────────────────────────
# DOC TYPES
# ─────────────────────────────────────────────────────────────────────────────

DOC_TYPES = [
    "trip_diary",
    "forum_qa",
    "listicle",
    "comparison",
    "itinerary_guide",
    "review_aggregator",
    "city_memoir",
]

DOC_TYPE_DESCRIPTIONS = {
    "trip_diary":        "First-person narrative of a full day/evening. 3–6 venues in sequence with times.",
    "forum_qa":          "Q&A thread: someone asks for venue recommendations, 2–4 venues per answer.",
    "listicle":          "Ranked/categorical list: 'Best X in Y'. 5–10 venues, one per item.",
    "comparison":        "Explicit head-to-head or neighbourhood comparison. 2–4 venues.",
    "itinerary_guide":   "Structured day plan: 10am X → lunch at Y → afternoon Z. Geographic clusters matter.",
    "review_aggregator": "Meta-summary of what 'people say' about a venue/neighbourhood. 1–3 venues.",
    "city_memoir":       "Loose literary account — venues mentioned incidentally. 3–6 venues, soft mentions.",
}

# Popularity ranges by doc type for assigning likes/saves/view_count
# Format: (likes_min, likes_max, saves_min, saves_max, views_min, views_max)
DOC_TYPE_POPULARITY = {
    "trip_diary":        (100, 400, 40,  150, 1000, 6000),
    "forum_qa":          (10,  60,  5,   25,  80,   500),
    "listicle":          (50,  200, 20,  80,  500,  3000),
    "comparison":        (30,  150, 15,  60,  300,  2000),
    "itinerary_guide":   (60,  250, 25,  100, 600,  4000),
    "review_aggregator": (80,  300, 30,  120, 800,  5000),
    "city_memoir":       (100, 350, 40,  130, 1000, 4500),
}

# Stale wrong-info docs get higher ranges (popular but potentially outdated)
STALE_POPULARITY = (150, 500, 60, 200, 1500, 6000)


# ─────────────────────────────────────────────────────────────────────────────
# TOOL SCHEMAS
# ─────────────────────────────────────────────────────────────────────────────

DOC_TOOL_SCHEMAS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "HELP",
            "description": "Query the doc-planning handbook. Use '-h tools', '-h pool_fields', '-h tags', '-h doc_types'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "e.g. '-h tools', '-h doc_types', '-h tags'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "THINK",
            "description": "Log a reasoning step. Use to plan venue groupings, check geographic coherence, decide wrong-info placement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string"}
                },
                "required": ["thought"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_pool",
            "description": "Return venues matching ALL given filters. Default show_venues=true — returns the full [{venue_id, name}] list. Set show_venues=false to get counts only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {
                        "type": "object",
                        "description": "Dict of filter conditions (AND logic). Keys: category, tag, regulation, district, traffic_tier, price_tier, booking_required."
                    },
                    "show_venues": {
                        "type": "boolean",
                        "description": "Whether to return the venue list. Default true. Set false for counts only."
                    }
                },
                "required": ["filters"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_venue",
            "description": "Return full detail for one venue: tags, coords, cost, regulations, wrong_info flag.",
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_id": {"type": "string"}
                },
                "required": ["venue_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_travel",
            "description": "Estimate travel time between two venues. Use for itinerary_guide geographic coherence checks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_id_a": {"type": "string"},
                    "venue_id_b": {"type": "string"},
                    "mode": {"type": "string", "enum": ["walking", "transit", "cycling"]}
                },
                "required": ["venue_id_a", "venue_id_b"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "SUBMIT",
            "description": (
                "Submit the final list of doc briefs. Runs validation. "
                "Loop ends on clean pass. Returns errors if invalid."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "briefs": {
                        "type": "array",
                        "description": "List of doc brief objects.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "doc_type":          {"type": "string", "enum": DOC_TYPES},
                                "angle":             {"type": "string", "description": "Short topic description"},
                                "venue_ids":         {"type": "array",  "items": {"type": "string"}},
                                "wrong_info_venues": {"type": "array",  "items": {"type": "string"},
                                                      "description": "Subset of venue_ids with wrong-info planned"},
                                "date":              {"type": "string", "description": "Publication date YYYY or YYYY-MM"},
                                "stale":             {"type": "boolean","description": "True if this is an older doc with potentially outdated info"}
                            },
                            "required": ["doc_type", "angle", "venue_ids"]
                        }
                    }
                },
                "required": ["briefs"]
            }
        }
    }
]


def _openai_to_anthropic_tools(schemas: list[dict]) -> list[dict]:
    return [
        {"name": s["function"]["name"],
         "description": s["function"].get("description", ""),
         "input_schema": s["function"].get("parameters", {"type": "object", "properties": {}})}
        for s in schemas
    ]


# ─────────────────────────────────────────────────────────────────────────────
# HANDBOOK
# ─────────────────────────────────────────────────────────────────────────────

def _build_doc_handbook(pool: list[dict], wrong_info_vids: set[str]) -> dict[str, str]:
    """Build dynamic handbook content from actual pool."""
    all_tags  = sorted({tag for v in pool for tag in v.get("tags", [])})
    districts = sorted({v.get("district", "") for v in pool if v.get("district")})
    categories = sorted({v.get("category", "") for v in pool if v.get("category")})

    hb = {}

    hb["tools"] = """Available tools:
  HELP(query)                       — this handbook
  THINK(thought)                    — log reasoning step
  query_pool(filters)               — find venues by category/tag/district/tier (names only)
  get_venue(venue_id)               — full detail including coords and wrong_info flag
  estimate_travel(id_a, id_b, mode) — haversine travel time between two venues
  SUBMIT(briefs)                    — submit final doc plan for validation

WORKFLOW:
1. THINK: plan your approach — what doc types you'll write, what themes make sense
2. Use query_pool to discover venue subsets for each planned doc
3. Use get_venue to inspect specific venues (esp. for itinerary_guide coord checks)
4. Use estimate_travel for itinerary_guide geographic coherence
5. Call SUBMIT with all briefs when your plan is complete
6. Fix any validation errors and retry SUBMIT"""

    hb["doc_types"] = "\n".join([
        "Doc types (use exact string in 'doc_type' field):",
        ""
    ] + [f"  {dt:20s} — {desc}" for dt, desc in DOC_TYPE_DESCRIPTIONS.items()] + [
        "",
        "Target: ~20 briefs total, at least one of each type.",
        "Same-type docs will be batched together in Phase 2 generation.",
        "Give each brief a distinct 'angle' so batched docs don't repeat each other.",
    ])

    hb["pool_fields"] = f"""Filterable fields for query_pool:
  category     — one of: {', '.join(categories)}
  tag          — any tag from '-h tags'
  regulation   — any regulation key from '-h regulations'
  district     — one of: {', '.join(districts)}
  traffic_tier — high | mid | low
  price_tier   — free | budget | mid | upscale | luxury

All conditions are AND. Single-field calls are fine."""

    hb["tags"] = f"""All tags in this city's pool ({len(all_tags)} total):
{chr(10).join('  ' + t for t in all_tags)}"""

    hb["regulations"] = """Regulation filter keys:
  wheelchair_accessible  — fully accessible (bool)
  pet_friendly           — pets allowed (bool)
  photography_allowed    — photography permitted (bool)
  family_friendly        — suitable for children (bool)
  booking_required       — advance booking required (bool)"""

    wi_list = sorted(wrong_info_vids)
    hb["wrong_info"] = f"""Venues with wrong info planned ({len(wi_list)} total):
{chr(10).join('  ' + v for v in wi_list) if wi_list else '  (none)'}

These venues have planted wrong info in their per-venue source docs.
You may include them in 'wrong_info_venues' for a brief to create additional
cross-doc contradictions. The truth carrier already exists in per-venue docs."""

    hb["brief_format"] = """{
  "doc_type":          "trip_diary",          -- required, one of the 7 types
  "angle":             "Evening in Shoreditch: craft pubs + late dinner",  -- required
  "venue_ids":         ["lon_b01", "lon_r04", "lon_b03"],  -- required, ≥1 venue
  "wrong_info_venues": ["lon_r04"],            -- optional, subset of venue_ids
  "date":              "2024-03",              -- optional, YYYY or YYYY-MM
  "stale":             false                   -- optional, true = older possibly-outdated doc
}

Rules:
- venue_ids: all must exist in pool
- wrong_info_venues: must be subset of venue_ids AND have wrong info planned in DB
- itinerary_guide: venues should be geographically clustered (use estimate_travel)
- stale=true: use for older listicles/diaries that may carry wrong hours (2021-2023)"""

    return hb


def _query_handbook(query: str, handbook: dict[str, str]) -> str:
    q = query.lower().strip().lstrip("-h").strip()
    if "tool"     in q: return handbook.get("tools", "")
    if "doc_type" in q or "type" in q: return handbook.get("doc_types", "")
    if "pool"     in q or "filter" in q or "field" in q: return handbook.get("pool_fields", "")
    if "tag"      in q: return handbook.get("tags", "")
    if "reg"      in q: return handbook.get("regulations", "")
    if "wrong"    in q or "wi" in q: return handbook.get("wrong_info", "")
    if "brief"    in q or "format" in q or "schema" in q: return handbook.get("brief_format", "")
    return (f"Handbook sections: {', '.join(handbook.keys())}\n"
            f"Query with '-h <section>' e.g. '-h doc_types', '-h tags', '-h brief_format'")


# ─────────────────────────────────────────────────────────────────────────────
# SUBMIT VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def _validate_briefs(briefs: list[dict], pool_map: dict,
                      wrong_info_vids: set[str]) -> list[str]:
    """
    Validate a list of doc briefs.
    Returns list of error strings (empty = valid).
    """
    errors = []
    if not briefs:
        errors.append("No briefs submitted — need ~20 doc briefs.")
        return errors

    if len(briefs) < 8:
        errors.append(f"Too few briefs ({len(briefs)}) — aim for ~20.")

    type_counts: dict[str, int] = {}
    for i, b in enumerate(briefs):
        label = f"Brief {i+1} ({b.get('doc_type','?')} — {b.get('angle','?')[:40]})"

        # doc_type
        dt = b.get("doc_type", "")
        if dt not in DOC_TYPES:
            errors.append(f"{label}: unknown doc_type '{dt}'. Must be one of: {DOC_TYPES}")
        type_counts[dt] = type_counts.get(dt, 0) + 1

        # venue_ids
        vids = b.get("venue_ids", [])
        if not vids:
            errors.append(f"{label}: venue_ids is empty — must include at least 1 venue.")
        for vid in vids:
            if vid not in pool_map:
                errors.append(f"{label}: venue_id '{vid}' not in pool.")

        # wrong_info_venues
        wi_vids = b.get("wrong_info_venues", [])
        for vid in wi_vids:
            if vid not in vids:
                errors.append(f"{label}: wrong_info_venue '{vid}' not in venue_ids.")
            if vid not in wrong_info_vids:
                errors.append(
                    f"{label}: venue '{vid}' has no wrong info planned — "
                    f"cannot assign wrong_info role. Use '-h wrong_info' to see valid venues."
                )

    # At least one of each doc type
    missing_types = [dt for dt in DOC_TYPES if type_counts.get(dt, 0) == 0]
    if missing_types:
        errors.append(
            f"Missing doc types: {missing_types}. "
            f"Include at least one brief of each type."
        )

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_doc_tool(
    tool_name: str,
    tool_input: dict,
    pool: list[dict],
    pool_map: dict,
    wrong_info_vids: set[str],
    handbook: dict,
) -> tuple[dict, bool, list[dict] | None]:
    """
    Dispatch a doc planning tool call.
    Returns (result_dict, should_terminate, accepted_briefs).
    should_terminate=True only when SUBMIT passes validation.
    """
    if tool_name == "HELP":
        content = _query_handbook(tool_input.get("query", ""), handbook)
        return {"status": "ok", "content": content}, False, None

    elif tool_name == "THINK":
        thought = tool_input.get("thought", "")
        return {"status": "ok", "logged": thought[:200] + "..." if len(thought) > 200 else thought}, False, None

    elif tool_name == "query_pool":
        filters    = tool_input.get("filters", {})
        show_venues = bool(tool_input.get("show_venues", True))  # default True for doc agent
        results    = _query_pool(filters, pool)
        return {
            "status": "ok",
            "count":  len(results),
            "venues": results if show_venues else [],
            "note":   "Use get_venue(venue_id) for coords, tags, wrong_info flag."
                      + ("" if show_venues else " Pass show_venues=true to see the venue list."),
        }, False, None

    elif tool_name == "get_venue":
        vid    = tool_input.get("venue_id", "")
        detail = _get_venue_detail(vid, pool_map)
        if detail is None:
            return {"status": "error", "message": f"venue_id '{vid}' not found in pool."}, False, None
        # Add wrong_info flag explicitly
        detail["has_wrong_info"] = vid in wrong_info_vids
        return {"status": "ok", "venue": detail}, False, None

    elif tool_name == "estimate_travel":
        id_a = tool_input.get("venue_id_a", "")
        id_b = tool_input.get("venue_id_b", "")
        mode = tool_input.get("mode", "transit")
        va   = pool_map.get(id_a)
        vb   = pool_map.get(id_b)
        if va is None:
            return {"status": "error", "message": f"venue_id_a '{id_a}' not found."}, False, None
        if vb is None:
            return {"status": "error", "message": f"venue_id_b '{id_b}' not found."}, False, None
        lat_a, lng_a = va.get("lat"), va.get("lng")
        lat_b, lng_b = vb.get("lat"), vb.get("lng")
        if lat_a is None or lat_b is None:
            return {"status": "error",
                    "message": "One or both venues have no coordinates."}, False, None
        dist_km     = haversine_km(lat_a, lng_a, lat_b, lng_b)
        road_km     = dist_km * 1.3
        walk_min    = estimate_minutes(road_km, 5.0)
        transit_min = estimate_minutes(road_km, 20.0) + 5
        cycle_min   = estimate_minutes(road_km, 15.0)
        result_min  = {"walking": walk_min, "transit": transit_min,
                       "cycling": cycle_min}.get(mode, transit_min)
        return {
            "status": "ok",
            "venue_a": va.get("name"), "venue_b": vb.get("name"),
            "distance_km": round(dist_km, 2),
            "walk_minutes": round(walk_min, 1),
            "transit_minutes": round(transit_min, 1),
            "result_minutes": round(result_min, 1),
            "requested_mode": mode,
            "note": "For itinerary_guide: walking ≤20min between consecutive venues is good."
        }, False, None

    elif tool_name == "SUBMIT":
        raw_briefs = tool_input.get("briefs", [])
        # Accept both list and JSON string
        if isinstance(raw_briefs, str):
            try:
                raw_briefs = json.loads(raw_briefs)
            except json.JSONDecodeError as e:
                return {"status": "validation_failed", "errors": [f"JSON parse error: {e}"],
                        "message": "Fix JSON and retry."}, False, None

        errors = _validate_briefs(raw_briefs, pool_map, wrong_info_vids)
        if errors:
            return {
                "status":  "validation_failed",
                "errors":  errors,
                "message": (f"{len(errors)} issue(s). Fix them and call SUBMIT again. "
                            "Use '-h brief_format' to review the brief schema.")
            }, False, None

        return {
            "status":  "accepted",
            "count":   len(raw_briefs),
            "message": f"{len(raw_briefs)} briefs accepted."
        }, True, raw_briefs

    else:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}, False, None


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _build_doc_system_prompt(city: str, pool: list[dict],
                               tasks: list[dict],
                               wrong_info_vids: set[str],
                               centre_lat: float, centre_lng: float) -> str:
    import math as _math
    lng_factor = round(_math.cos(_math.radians(centre_lat)) * 111, 1)

    cat_counts: dict[str, int] = {}
    n_with_coords = 0
    for v in pool:
        cat_counts[v.get("category", "?")] = cat_counts.get(v.get("category", "?"), 0) + 1
        if v.get("lat") and v.get("lng"):
            n_with_coords += 1

    # Task summary — just structural types and constraint patterns
    task_constraint_summary = []
    for t in tasks[:40]:  # cap at 40 for context length
        stype = t.get("structural_type", "?")
        pcs   = t.get("rubric", {}).get("personal_constraints", [])
        patterns = list({c.get("pattern", "") for c in pcs if c.get("pattern")})
        task_constraint_summary.append(f"  {stype}: {', '.join(patterns[:4])}")

    # Compact pool listing
    pool_lines = []
    for v in pool[:50]:
        lat, lng = v.get("lat"), v.get("lng")
        coord    = f"{lat:.3f},{lng:.3f}" if lat and lng else "?,?"
        wi       = " [WI]" if v["venue_id"] in wrong_info_vids else ""
        pool_lines.append(
            f"  {v['venue_id']:12s} | {v['name'][:30]:30s} | {v.get('category',''):12s} | "
            f"{v.get('district',''):15s} | {v.get('traffic_tier',''):4s} | {coord}{wi}"
        )

    return f"""You are a travel corpus planning agent for TravelBench.

Your job: plan ~20 multi-venue documents for {city.title()} that make the benchmark
corpus richer and more realistic. You are NOT writing the docs — you are planning what
docs should be written (Phase 1). The actual content is generated in Phase 2.

CITY: {city.title()}
POOL: {len(pool)} venues ({n_with_coords} with coordinates)
Pool breakdown: {json.dumps(cat_counts)}
Wrong-info venues: {len(wrong_info_vids)} (use '-h wrong_info' for list)

TRAVEL FORMULA (lng_factor={lng_factor} for this city):
dist_km ≈ sqrt((Δlat×111)² + (Δlng×{lng_factor})²)
Or use estimate_travel(venue_id_a, venue_id_b) for specific pairs.

VENUE POOL (condensed — use get_venue for full detail):
Format: venue_id | name | category | district | tier | lat,lng | [WI]=wrong info
{chr(10).join(pool_lines)}

TASK CONSTRAINT SUMMARY (for context — plan docs that cover these combinations):
{chr(10).join(task_constraint_summary[:20]) if task_constraint_summary else '  (no tasks provided)'}

PLANNING GOALS:
1. Write ~20 briefs covering all 7 doc types (at least one each)
2. Give each brief a distinct angle — docs of the same type should cover different
   neighbourhoods, themes, or venue combinations
3. Venue coverage: organic — high-traffic venues appear more (realistic). Don't
   force coverage of every venue; low-traffic venues appear when they fit naturally
4. At least 2–3 briefs should include venues from 'wrong_info_venues' to create
   cross-doc contradictions (stale=true for older docs with wrong info)
5. For itinerary_guide briefs: verify venue clusters are geographically coherent
   using estimate_travel before committing (walking ≤20min between consecutive venues)
6. Vary publication dates: mix recent (2024–2025) and older (2021–2023) docs.
   Older docs with wrong-info venues are natural stale-info traps

Use HELP '-h doc_types' to see all 7 types and their descriptions.
Use HELP '-h brief_format' to see the brief schema.
Use THINK liberally — plan before querying, reason before submitting.
"""


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-MODEL LOOPS
# ─────────────────────────────────────────────────────────────────────────────

def _run_openai_doc_loop(messages, system_prompt, model, api_key, base_url,
                          pool, pool_map, wrong_info_vids, handbook,
                          verbose) -> list[dict] | None:
    client     = OpenAI(api_key=api_key, **({} if not base_url else {"base_url": base_url}))
    max_tokens = 16000 if "reasoner" in model.lower() else 6000
    accepted   = None
    turn       = 0
    while turn < MAX_TURNS:
        turn += 1
        resp   = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            tools=DOC_TOOL_SCHEMAS_OPENAI,
            messages=[{"role": "system", "content": system_prompt}] + messages,
        )
        msg    = resp.choices[0].message
        finish = resp.choices[0].finish_reason
        messages.append(msg.model_dump(exclude_unset=False))
        if verbose and msg.tool_calls:
            for tc in msg.tool_calls:
                print(f"    [turn {turn}] {tc.function.name}")
        if finish == "stop" or not msg.tool_calls:
            break
        for tc in msg.tool_calls:
            try:
                ti = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                ti = {}
            result, terminate, briefs = _dispatch_doc_tool(
                tc.function.name, ti, pool, pool_map, wrong_info_vids, handbook
            )
            if verbose:
                print(f"    [turn {turn}] {tc.function.name} → {result.get('status','?')}")
            messages.append({"role": "tool", "tool_call_id": tc.id,
                              "content": json.dumps(result, ensure_ascii=False)})
            if terminate:
                accepted = briefs
        if accepted is not None:
            return accepted
    return None


def _run_anthropic_doc_loop(initial_message, system_prompt, model, api_key,
                              pool, pool_map, wrong_info_vids, handbook,
                              verbose) -> list[dict] | None:
    client   = _anthropic_mod.Anthropic(api_key=api_key)
    tools    = _openai_to_anthropic_tools(DOC_TOOL_SCHEMAS_OPENAI)
    messages = [{"role": "user", "content": initial_message}]
    accepted = None
    turn     = 0
    while turn < MAX_TURNS:
        turn    += 1
        resp     = client.messages.create(
            model=model, max_tokens=8000,
            system=system_prompt, tools=tools, messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})
        if verbose:
            for b in resp.content:
                if hasattr(b, "name"):
                    print(f"    [turn {turn}] {b.name}")
        if resp.stop_reason == "end_turn":
            break
        if resp.stop_reason != "tool_use":
            break
        tool_results   = []
        terminate_after = False
        for b in resp.content:
            if b.type != "tool_use":
                continue
            ti     = b.input if isinstance(b.input, dict) else {}
            result, terminate, briefs = _dispatch_doc_tool(
                b.name, ti, pool, pool_map, wrong_info_vids, handbook
            )
            if verbose:
                print(f"    [turn {turn}] {b.name} → {result.get('status','?')}")
            tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                  "content": json.dumps(result, ensure_ascii=False)})
            if terminate:
                accepted = briefs
                terminate_after = True
        messages.append({"role": "user", "content": tool_results})
        if terminate_after:
            return accepted
    return None


def _run_gemini_doc_loop(initial_message, system_prompt, model, api_key,
                          pool, pool_map, wrong_info_vids, handbook,
                          verbose) -> list[dict] | None:
    from google.genai import types as gt
    client    = _google_genai.Client(api_key=api_key)
    tool_decls = []
    for s in DOC_TOOL_SCHEMAS_OPENAI:
        fn = s["function"]
        tool_decls.append(gt.Tool(function_declarations=[
            gt.FunctionDeclaration(name=fn["name"],
                                   description=fn.get("description", ""),
                                   parameters=fn.get("parameters", {}))
        ]))
    contents = [gt.Content(role="user", parts=[gt.Part(text=initial_message)])]
    config   = gt.GenerateContentConfig(system_instruction=system_prompt,
                                         max_output_tokens=8000, tools=tool_decls)
    accepted = None
    turn     = 0
    while turn < MAX_TURNS:
        turn += 1
        resp  = _google_genai.Client(api_key=api_key).models.generate_content(
            model=model, contents=contents, config=config
        )
        contents.append(resp.candidates[0].content)
        fn_calls = [p for p in resp.candidates[0].content.parts
                    if hasattr(p, "function_call") and p.function_call]
        if not fn_calls:
            break
        fn_responses   = []
        terminate_after = False
        for part in fn_calls:
            fc     = part.function_call
            ti     = dict(fc.args) if fc.args else {}
            result, terminate, briefs = _dispatch_doc_tool(
                fc.name, ti, pool, pool_map, wrong_info_vids, handbook
            )
            if verbose:
                print(f"    [turn {turn}] {fc.name} → {result.get('status','?')}")
            fn_responses.append(gt.Part(function_response=gt.FunctionResponse(
                name=fc.name,
                response={"result": json.dumps(result, ensure_ascii=False)}
            )))
            if terminate:
                accepted = briefs
                terminate_after = True
        contents.append(gt.Content(role="user", parts=fn_responses))
        if terminate_after:
            return accepted
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_doc_planning_agent(
    city: str,
    pool: list[dict],
    tasks: list[dict],
    model: str,
    api_key: str,
    verbose: bool = False,
) -> list[dict] | None:
    """
    Run the doc planning agent loop.

    Returns accepted list of doc briefs, or None if MAX_TURNS exhausted.

    Args:
        city:    city name
        pool:    venue pool list (from load_venue_pool / load_city_pool)
        tasks:   all tasks for this city across all windows (context only)
        model:   model string
        api_key: API key
        verbose: print turn-by-turn tool calls
    """
    if not api_key:
        return None

    pool_map       = {v["venue_id"]: v for v in pool}
    wrong_info_vids = {v["venue_id"] for v in pool if v.get("has_wrong_info")}
    centre_lat, centre_lng = _city_centre_from_pool(pool)
    handbook       = _build_doc_handbook(pool, wrong_info_vids)
    system_prompt  = _build_doc_system_prompt(
        city, pool, tasks, wrong_info_vids, centre_lat, centre_lng
    )

    initial_message = (
        f"Plan ~20 multi-venue documents for {city.title()}. "
        f"Start with THINK to outline your approach, then use query_pool and "
        f"get_venue to discover venue combinations, and call SUBMIT when ready. "
        f"Use HELP '-h doc_types' to see all 7 document types."
    )

    if verbose:
        print(f"  Doc planning agent: {model} | city={city} | "
              f"pool={len(pool)} | wrong_info={len(wrong_info_vids)}")

    model_lower = model.lower()
    if "claude" in model_lower or "anthropic" in model_lower:
        if not HAS_ANTHROPIC:
            raise ImportError("anthropic package required for Claude models")
        return _run_anthropic_doc_loop(
            initial_message, system_prompt, model, api_key,
            pool, pool_map, wrong_info_vids, handbook, verbose
        )
    elif "gemini" in model_lower:
        if not HAS_GEMINI:
            raise ImportError("google-genai package required for Gemini models")
        return _run_gemini_doc_loop(
            initial_message, system_prompt, model, api_key,
            pool, pool_map, wrong_info_vids, handbook, verbose
        )
    else:
        if not HAS_OPENAI:
            raise ImportError("openai package required for GPT/DeepSeek models")
        base_url = "https://api.deepseek.com/v1" if "deepseek" in model_lower else None
        messages = [{"role": "user", "content": initial_message}]
        return _run_openai_doc_loop(
            messages, system_prompt, model, api_key, base_url,
            pool, pool_map, wrong_info_vids, handbook, verbose
        )
