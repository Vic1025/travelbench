"""
scripts/generation/task_agent.py

Agent-loop task generation for TravelBench.

The agent uses tools to explore the venue pool, reason about feasibility,
and produce a validated task JSON via SUBMIT. The loop only terminates when
SUBMIT passes validation, or when MAX_TURNS is exhausted.

Tools:
  HELP(query)                         — dynamic handbook
  THINK(thought)                      — log reasoning steps
  query_pool(filters)                 — count matching venues; add show_venues=true to see the list
  get_venue(venue_id)                 — full detail on one venue
  estimate_travel(id_a, id_b, mode)   — haversine travel estimate
  SUBMIT(task_json)                   — validate + terminate loop on pass

Multi-model: Anthropic, OpenAI (GPT + DeepSeek), Gemini all supported.
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from scripts.generation.db import get_connection, DB_PATH, get_city_db_path
from scripts.generation.build_travel_matrix import haversine_km, estimate_minutes
from scripts.generation.pool_utils import (
    _city_centre_from_pool, _query_pool, _get_venue_detail,
)

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
    from google.genai import types as _genai_types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

MAX_TURNS = 20


def _extract_usage(resp, provider: str) -> dict:
    """Normalize per-call token usage across providers.

    Returns {input_tokens, output_tokens, total_tokens}. Missing fields default
    to 0 so summing across turns never crashes on a partial response.
    """
    try:
        if provider == "openai":
            u = getattr(resp, "usage", None)
            if not u:
                return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            inp = getattr(u, "prompt_tokens", 0) or 0
            out = getattr(u, "completion_tokens", 0) or 0
            return {"input_tokens": inp, "output_tokens": out,
                    "total_tokens": getattr(u, "total_tokens", inp + out) or (inp + out)}
        if provider == "anthropic":
            u = getattr(resp, "usage", None)
            if not u:
                return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            inp = getattr(u, "input_tokens", 0) or 0
            out = getattr(u, "output_tokens", 0) or 0
            cc  = getattr(u, "cache_creation_input_tokens", 0) or 0
            cr  = getattr(u, "cache_read_input_tokens", 0) or 0
            return {"input_tokens": inp, "output_tokens": out,
                    "cache_creation_input_tokens": cc,
                    "cache_read_input_tokens": cr,
                    "total_tokens": inp + out + cc + cr}
        if provider == "gemini":
            u = getattr(resp, "usage_metadata", None)
            if not u:
                return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            inp = getattr(u, "prompt_token_count", 0) or 0
            out = getattr(u, "candidates_token_count", 0) or 0
            return {"input_tokens": inp, "output_tokens": out,
                    "total_tokens": getattr(u, "total_token_count", inp + out) or (inp + out)}
    except Exception:
        pass
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
URGENCY_TURNS = 8  # warn + urge SUBMIT when this many turns remain

# ─────────────────────────────────────────────────────────────────────────────
# TOOL SCHEMAS  (OpenAI / DeepSeek format — Anthropic uses a different format)
# ─────────────────────────────────────────────────────────────────────────────

TASK_TOOL_SCHEMAS_OPENAI = [
    {
        "type": "function",
        "function": {
            "name": "HELP",
            "description": (
                "Query the task-generation handbook. "
                "Use '-h tools' for tool list, '-h pool_fields' for filterable fields, "
                "'-h tags' for available tags, '-h regulations' for regulation keys, "
                "'-h venue_sample' for a sample get_venue response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "e.g. '-h tools', '-h tags', '-h pool_fields'"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "THINK",
            "description": "Log a reasoning step. Use to record decisions, calculations, constraint choices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Your reasoning step."}
                },
                "required": ["thought"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_pool",
            "description": (
                "Count venues matching ALL given filters and optionally return their "
                "names. By default returns counts only (food/site breakdown + total). "
                "Set show_venues=true to also get the venue list. "
                "Use include= to attach extra fields when show_venues=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {
                        "type": "object",
                        "description": (
                            "Dict of filter conditions (all must match). "
                            "Supported keys: category, tag, regulation, district, "
                            "traffic_tier, price_tier, booking_required. "
                            "Use HELP '-h pool_fields' for all valid values."
                        )
                    },
                    "show_venues": {
                        "type": "boolean",
                        "description": (
                            "Whether to return the matching venue list. "
                            "Default false — returns counts only. "
                            "Set true when you need venue_ids to reference in constraints. "
                            "Results >30 return name+ID only (lite mode) even when true; "
                            "use include= on a more selective query for extra fields."
                        )
                    },
                    "include": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["price", "duration", "coords"]},
                        "description": (
                            "Extra field groups per venue when show_venues=true: "
                            "'price' adds avg_cost_local + price_tier; "
                            "'duration' adds recommended_visit_minutes; "
                            "'coords' adds lat + lng + district. "
                            "Type 2 tasks: include=['duration','coords']. "
                            "Type 4 tasks: include=['price']."
                        )
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
            "description": "Return full detail for one venue: tags, regulations, coords, cost, hours summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_id": {"type": "string", "description": "The venue_id from query_pool results."}
                },
                "required": ["venue_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_travel",
            "description": "Estimate travel time between two venues using haversine distance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_id_a": {"type": "string"},
                    "venue_id_b": {"type": "string"},
                    "mode": {"type": "string", "enum": ["walking", "transit", "cycling"],
                             "description": "Travel mode. Default: transit."}
                },
                "required": ["venue_id_a", "venue_id_b"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_official_site",
            "description": (
                "Return the official site content for a venue on a given date. "
                "Use for Type 6 tasks to verify seasonal closures, modified hours, "
                "or sold-out events that the solving agent must discover. "
                "Returns page body, any active event on that date, and ticket availability."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_id": {"type": "string", "description": "The venue_id from query_pool results."},
                    "date": {"type": "string", "description": "ISO date string (YYYY-MM-DD). Used to check active events and ticket availability on that specific date."}
                },
                "required": ["venue_id", "date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "SUBMIT",
            "description": (
                "Submit the final task JSON. Runs full validation. "
                "If validation passes, the loop ends and the task is accepted. "
                "If validation fails, errors are returned so you can fix and retry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_json": {
                        "type": "string",
                        "description": "The complete task JSON as a string."
                    }
                },
                "required": ["task_json"]
            }
        }
    }
]


def _openai_to_anthropic_tools(schemas: list[dict]) -> list[dict]:
    """Convert OpenAI tool schema format to Anthropic format."""
    result = []
    for s in schemas:
        fn = s["function"]
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# DYNAMIC HANDBOOK
# ─────────────────────────────────────────────────────────────────────────────

def _build_task_handbook(pool: list[dict], window: dict, city: str) -> dict[str, str]:
    """
    Build handbook content dynamically from the actual pool.
    Returns dict of {topic: content_string}.
    """
    # Collect actual tags from pool
    all_tags: set[str] = set()
    for v in pool:
        all_tags.update(v.get("tags", []))
    sorted_tags = sorted(all_tags)

    # Collect distinct districts, categories, tiers
    districts     = sorted({v.get("district", "") for v in pool if v.get("district")})
    categories    = sorted({v.get("category", "") for v in pool if v.get("category")})
    traffic_tiers = ["high", "mid", "low"]
    price_tiers   = ["free", "budget", "mid", "upscale", "luxury"]
    pace_values   = ["relaxed", "moderate", "intense"]
    noise_values  = ["quiet", "moderate", "lively", "loud"]

    # Regulation keys (static — these are DB column names)
    regulation_keys = [
        "wheelchair_accessible", "pet_friendly", "photography_allowed",
        "family_friendly", "age_restriction", "dress_code",
        "noise_level", "reservation_required", "outside_food_allowed",
    ]

    # Count venues per category for useful context
    cat_counts = {}
    for v in pool:
        c = v.get("category", "?")
        cat_counts[c] = cat_counts.get(c, 0) + 1

    # Sample venue (first in pool)
    sample = pool[0] if pool else {}
    sample_out = {
        "venue_id":                  sample.get("venue_id", "lon_r01"),
        "name":                      sample.get("name", "Example Venue"),
        "category":                  sample.get("category", "restaurant"),
        "district":                  sample.get("district", "Shoreditch"),
        "traffic_tier":              sample.get("traffic_tier", "mid"),
        "recommended_pace":          sample.get("recommended_pace", "moderate"),
        "price_tier":                sample.get("price_tier", "mid"),
        "avg_cost_local":              sample.get("avg_cost_local", 25.0),
        "lat":                       sample.get("lat"),
        "lng":                       sample.get("lng"),
        "recommended_visit_minutes": sample.get("recommended_visit_minutes", 90),
        "tags":                      sample.get("tags", []),
        "regulations": {
            "wheelchair_accessible": bool(sample.get("wheelchair_accessible", 0)),
            "pet_friendly":          bool(sample.get("pet_friendly", 0)),
            "photography_allowed":   bool(sample.get("photography_allowed", 1)),
            "family_friendly":       bool(sample.get("family_friendly", 1)),
            "age_restriction":       sample.get("age_restriction"),
            "dress_code":            sample.get("dress_code"),
            "noise_level":           sample.get("noise_level", "moderate"),
            "reservation_required":  bool(sample.get("reservation_required", 0)),
            "outside_food_allowed":  bool(sample.get("outside_food_allowed", 0)),
        },
        "booking_required":    bool(sample.get("booking_required", 0)),
        "has_official_site":   bool(sample.get("has_official_site", 1)),
        "window_flags":        sample.get("window_flags", {}),
        "unavailable_dates":   [],
    }

    # Window anchor dates (for Type 6 context)
    anchor_dates = [e["date"] for e in window.get("anchor_events", [])]
    window_dates = window.get("dates", [])

    handbook = {}

    handbook["tools"] = f"""Available tools (7 total):

  HELP(query)                              — query this handbook
  THINK(thought)                           — log a reasoning step
  query_pool(filters, show_venues=false, include=[])  — count matching venues (default: counts only).
                                                         Set show_venues=true to get the venue list.
                                                         Results >30 return name+ID only even when true.
  get_venue(venue_id)                      — full detail on one venue (tags, regulations, window_flags)
  get_official_site(venue_id, date)        — official site content + any events/closures on that date
  estimate_travel(id_a, id_b, mode)        — haversine travel time estimate
  SUBMIT(task_json)                        — validate + submit final task JSON

═══════════════════════════════════════════════════════════════════════════
CRITICAL: YOU MUST USE ACTUAL TOOL CALLS — NOT PROSE
═══════════════════════════════════════════════════════════════════════════
Every tool name above is an actual callable function in this environment.
Writing "I would now call SUBMIT(...)" or pasting JSON into your message
body does NOTHING. The validation system only runs when you invoke SUBMIT
as a real tool call. The loop will NOT end until you do this.

If you write a complete task JSON in an assistant message but do not call
SUBMIT, you will receive a reminder asking you to call the tool. Repeated
prose-only responses terminate the loop with no task produced.
═══════════════════════════════════════════════════════════════════════════

WORKFLOW (each step is one tool call = one turn):
1. THINK — decide your structural approach and persona
2. query_pool — discover what exists in the pool (repeat as needed)
3. get_venue — inspect specific venues you're considering
4. get_official_site(venue_id, date) — check seasonal closures/events (Type 6 only)
5. estimate_travel — check travel time between any two venues (useful for all types)
6. SUBMIT — send the complete task JSON for validation
7. If SUBMIT returns errors, read them, fix them, and call SUBMIT again

QUERY_POOL INCLUDE PARAMETER (P7):
The `include` argument lets you pull extra fields per venue in a single call
instead of follow-up get_venue roundtrips. Three valid values:

  "price"    → adds avg_cost_local, price_tier to each result
  "duration" → adds recommended_visit_minutes
  "coords"   → adds lat, lng, district

By default (include=[] or omitted), results contain only {{venue_id, name}}.

Recommended usage by structural type:

  Type 2 (time-ceiling selection):
    query_pool({{"category": "restaurant"}}, show_venues=true, include=["duration", "coords"])
    You'll need duration to estimate visit time and coords to compute
    nearest-neighbour travel between candidates.

  Type 4 (budget-ceiling allocation):
    query_pool({{"category": "museum"}}, show_venues=true, include=["price"])
    You need avg_cost_local to rank venues by cost and pick cheapest K.

  Other types: default include=[] is fine unless you need bulk pricing/geometry.

EXAMPLE SUBMIT CALL (the shape your final tool call must take):
  SUBMIT(task_json='{{
    "task_id": "lon_gen_001",
    "city": "london",
    "window_id": "london_easter_2026",
    "days": 1,
    "start_date": "2026-04-04",
    "structural_type": "type1_cascading_requirements",
    "difficulty": "medium",
    "public_input": {{
      "query": "...",
      "query_resources": {{
        // type2 only: "time_ceiling_minutes": <int>
        // type4 only: "budget_per_day": <number in local currency>
      }}
    }},
    "rubric": {{
      "hard_constraints":     [...],
      "personal_constraints": [...],
      "b_score_constraints":  [],
      "required_venue_ids":   []
    }}
  }}')

CRITICAL — structural_type MUST be the full canonical string, not an integer
or short form. Accepted values (exactly one per task):
  "type1_cascading_requirements"       — NOT 1, NOT "type1"
  "type2_subset_selection"             — NOT 2, NOT "type2"
  "type3_competing_requirements"       — NOT 3, NOT "type3"
  "type4_precision_allocation"         — NOT 4, NOT "type4"
  "type5_hard_feasibility_reduction"   — NOT 5, NOT "type5"
  "type6_context_window_tension"       — NOT 6, NOT "type6"
The validator auto-corrects integers and short forms with a warning, but this
costs you a turn. Submit the full string on your first SUBMIT.

For Type 2 tasks, public_input.query_resources MUST contain:
  "time_ceiling_minutes": <int>     // the numeric time budget from the query
For Type 4 tasks, public_input.query_resources MUST contain:
  "budget_per_day": <number>        // numeric per-day budget in the city's local currency

SUBMIT runs validation. Only SUBMIT ends the loop — THINK/query_pool/get_venue
do not. Use '-h validate' to see what validation checks for."""

    handbook["pool_fields"] = f"""Filterable fields for query_pool:

  category        — one of: {', '.join(categories)}
                    pool counts: {json.dumps(cat_counts)}
  tag             — any tag from the list in '-h tags'
  regulation      — any regulation key from '-h regulations' (matches if true/non-null)
  district        — one of: {', '.join(districts)}
  traffic_tier    — one of: {', '.join(traffic_tiers)}
  price_tier      — one of: {', '.join(price_tiers)}
  booking_required — true or false

All filters are AND — all conditions must match.
Single-field calls are fine.

Example: query_pool({{"category": "restaurant", "tag": "halal", "regulation": "wheelchair_accessible"}})
→ returns restaurants that are halal AND wheelchair accessible

Example: query_pool({{"traffic_tier": "low"}})
→ returns all low-traffic (hidden gem) venues"""

    handbook["tags"] = f"""All tags in this city's pool ({len(sorted_tags)} total):

{chr(10).join('  ' + t for t in sorted_tags)}

Use the 'tag' filter key in query_pool to filter by tag.
A venue may have multiple tags.
Tags come from Yelp listings and source docs — not all venues have tags."""

    handbook["regulations"] = f"""Regulation fields (use the 'regulation' filter key in query_pool):

  wheelchair_accessible  — venue is fully wheelchair accessible (bool)
  pet_friendly           — pets allowed (bool)
  photography_allowed    — photography permitted (bool)
  family_friendly        — suitable for children (bool)
  age_restriction        — minimum age in years (int or null — null means no restriction)
  dress_code             — dress code description (string or null)
  noise_level            — one of: {', '.join(noise_values)}
  reservation_required   — advance booking required (bool)
  outside_food_allowed   — outside food permitted (bool)

Note: the 'regulation' filter in query_pool matches venues where the regulation is TRUE
(or non-null for string fields). For noise_level filtering, use get_venue to check
the exact value on specific venues."""

    handbook["venue_sample"] = f"""Sample get_venue response (all fields returned):

{json.dumps(sample_out, indent=2)}

Key fields for task design:
  avg_cost_local              — per-person cost estimate (use for Type 4 budget checks)
  recommended_visit_minutes — typical visit duration (use for Type 2 time calculations)
  lat, lng                  — coordinates (use with estimate_travel)
  tags                      — searchable labels (use query_pool with 'tag' filter)
  regulations               — accessibility, dietary, dress code flags
  window_flags              — seasonal modifiers for this venue
  unavailable_dates         — dates this venue is sold out in the window"""

    handbook["window"] = f"""Current window context:

  Window: {window.get('label', '?')}
  Dates:  {window_dates[0] if window_dates else '?'} – {window_dates[-1] if window_dates else '?'}
  Anchor events: {', '.join(f"{e['name']} ({e['date']})" for e in window.get('anchor_events', []))}
  Anchor dates: {', '.join(anchor_dates) if anchor_dates else 'none'}

Character:
{window.get('character', '')}

Conditional stale-data hints:
{json.dumps(window.get('conditional_wrong_info_hints', []), indent=2)}"""

    handbook["validate"] = """Validation checks run on SUBMIT.

═══════════════════════════════════════════════════════════════════════════
EXACT CONSTRAINT SCHEMA — THIS IS THE #1 REASON SUBMIT FAILS
═══════════════════════════════════════════════════════════════════════════

Every personal_constraint MUST have these 8 fields:
  {
    "id":                "pc_001",              // unique within rubric
    "score_tier":        "P",                   // literal "P" (not "hard"/"medium")
    "hop":               2,                     // integer: 1 or 2
    "check_method":      "code",                // always "code" for P-constraints
    "source_in_profile": "art museums",         // verbatim phrase from query
    "description":       "Must include ≥1 art museum",
    "scope":             "all",                 // see SCOPE section below
    "condition":         {"has_tag": "art"},    // see CONDITION section below
    "aggregation":       {"at_least": 1},       // see AGGREGATION section below
    "consequence":       "p_score_full"         // always "p_score_full"
  }

DO NOT use "pattern"/"params" — that's the old format. The evaluator now uses
scope/condition/aggregation. Using the old format is a SUBMIT failure mode.

═══════════════════════════════════════════════════════════════════════════
SCOPE — which activities does this constraint apply to?
═══════════════════════════════════════════════════════════════════════════
  "all"                         every activity in the plan
  "activity_type=meal"          only meal-type activities
  "activity_type=visit"         only visit-type activities
  "per_day"                     applied independently per day
  "category=museum"             activities at museum-category venues
  "has_tag=halal"               activities at venues with the "halal" tag
  "venue_id=lon_abc123"         the specific venue with that id
  "time_window=18:00-23:59"     activities starting in that time range

  List = AND:  ["activity_type=meal", "has_tag=vegetarian-options"]
               → only meal activities at venues tagged with vegetarian-options

═══════════════════════════════════════════════════════════════════════════
CONDITION — what must each in-scope activity satisfy?
═══════════════════════════════════════════════════════════════════════════
  {"has_tag": "halal"}                     venue has this tag
  {"not_tag": "tourist-trap"}              venue does NOT have this tag
  {"field": "noise_level",                 venue field comparison
   "operator": "<=", "value": "moderate"}
  {"field": "price_tier",
   "operator": ">=", "value": "upscale"}
  {"field": "recommended_visit_minutes",
   "operator": "<=", "value": 60}
  {}                                       no condition (scope filter only)

  Composite:
  {"all": [cond1, cond2]}                  AND — both conditions must hold
  {"any": [cond1, cond2]}                  OR  — either condition suffices

  Ordered string enums (supports <=, >=, <, >):
    price_tier:          "free" < "budget" < "mid" < "upscale" < "luxury"
    noise_level:         "quiet" < "moderate" < "lively" < "loud"
    recommended_pace:    "relaxed" < "moderate" < "intense"
    traffic_tier:        "low" < "mid" < "high"
    dress_code:          "none" < "casual" < "smart_casual" < "formal"

═══════════════════════════════════════════════════════════════════════════
AGGREGATION — how must the in-scope activities collectively satisfy?
═══════════════════════════════════════════════════════════════════════════
  "all"                          every in-scope activity must satisfy
                                 (universal constraint — IS a pool filter)
  "none"                         no in-scope activity may satisfy
                                 (exclusion constraint — IS a pool filter)
  {"at_least": 2}                at least 2 in-scope activities satisfy
                                 (inclusion constraint — does NOT filter pool)
  {"at_most": 1}                 at most 1 in-scope activity satisfies
  {"at_least_days": 1}           at least 1 complete day where all
                                 in-scope activities satisfy the condition
  {"count_distinct": 3,          at least 3 distinct values of field.
   "field": "district"}          Valid fields: district, category, price_tier,
                                 traffic_tier, recommended_pace, cuisine.
                                 cuisine is the structured field on
                                   restaurants (P6-T1b). For "try N
                                   different cuisines": use this with
                                   scope='activity_type=meal'.
  {"at_most_distinct": 2,        at most 2 distinct values of field
   "field": "district"}
  {"sum": "estimated_cost_local", total satisfies operator+value
   "operator": "<=", "value": 80}
  {"ratio": 0.6}                 ≥60% of in-scope activities satisfy condition

score_tier meaning:
  "P" (personal) — normal P-score constraint. ALWAYS use "P".

hop meaning:
  1 — direct read from query ("vegetarian" → has_tag: vegetarian)
  2 — requires reasoning ("anniversary" → upscale restaurant)
  Every task needs at least one hop-2 P-constraint.

source_in_profile: verbatim phrase from query. Must NOT be "", "N/A",
  "inferred", "implicit", "assumed". For hop-2, ≥50% of content words
  must appear in the query.

═══════════════════════════════════════════════════════════════════════════
CONSTRAINT DIVERSITY RULES
═══════════════════════════════════════════════════════════════════════════
Aggregation diversity (if ≥3 P-constraints):
  At most 2 may use {{at_least}}. At least one must use a different
  aggregation type: all, none, ratio, count_distinct, sum, at_most,
  at_most_distinct, at_least_days. This prevents every task from being
  just "include N venues with tag X" repeated.

Scope diversity (if ≥3 P-constraints):
  At least one must use a scope other than "all" or "activity_type=meal".
  Use: per_day, time_window=*, category=*, venue_id=*, or compound [list].
  NEVER use has_tag= as a scope — it is a condition dimension, not a scope path.
  Wrong: scope="has_tag=outdoor-seating"  → Right: scope="all", condition={"has_tag":"outdoor-seating"}

═══════════════════════════════════════════════════════════════════════════
WORKING P-CONSTRAINT EXAMPLES — copy these shapes
═══════════════════════════════════════════════════════════════════════════
// "All meals must have a halal tag" (universal)
{
  "id": "pc_001", "score_tier": "P", "hop": 1, "check_method": "code",
  "source_in_profile": "halal meals",
  "description": "All meal venues must be halal-certified",
  "scope": "activity_type=meal",
  "condition": {"has_tag": "halal"},
  "aggregation": "all",
  "consequence": "p_score_full"
},
// "All venues must be wheelchair accessible" (universal: all venue types)
{
  "id": "pc_002", "score_tier": "P", "hop": 2, "check_method": "code",
  "source_in_profile": "using a wheelchair",
  "description": "All venues must be wheelchair accessible",
  "scope": "all",
  "condition": {"field": "wheelchair_accessible", "operator": "==", "value": true},
  "aggregation": "all",
  "consequence": "p_score_full"
},
// "Include at least 2 museums" (inclusion — does NOT filter the pool)
{
  "id": "pc_003", "score_tier": "P", "hop": 1, "check_method": "code",
  "source_in_profile": "loves art museums",
  "description": "At least 2 museum visits",
  "scope": "category=museum",
  "condition": {},
  "aggregation": {"at_least": 2},
  "consequence": "p_score_full"
},
// "No tourist-trap venues" (exclusion)
{
  "id": "pc_004", "score_tier": "P", "hop": 2, "check_method": "code",
  "source_in_profile": "avoid tourist traps",
  "description": "No tourist-trap venues in the plan",
  "scope": "all",
  "condition": {"has_tag": "tourist-trap"},
  "aggregation": "none",
  "consequence": "p_score_full"
},
// "Evening meals must be upscale" (scoped + universal)
{
  "id": "pc_005", "score_tier": "P", "hop": 2, "check_method": "code",
  "source_in_profile": "special occasion dinner",
  "description": "Evening meals must be upscale or above",
  "scope": ["activity_type=meal", "time_window=18:00-23:59"],
  "condition": {"field": "price_tier", "operator": ">=", "value": "upscale"},
  "aggregation": "all",
  "consequence": "p_score_full"
}

═══════════════════════════════════════════════════════════════════════════
SCHEMA CHECKS (validate_task_schema)
═══════════════════════════════════════════════════════════════════════════
  - Required top-level fields: city, days, start_date,
                               public_input.query, rubric
  - Standard hard constraints present: hours_check, no_overlap, travel_time_hard
    (these are the only constraints that use "type", not "pattern")
  - Every P-constraint has all 8 fields listed above
  - At least one hop-2 P-constraint
  - source_in_profile must not be a placeholder
  - Type 3 only: constraint tension required (two constraints with ≤25% venue-set overlap)
  - Type 6 only: must be grounded in the seasonal window
  - B-score python_script must define evaluate(plan, task, venues) and return float in [0,1]
  - required_venue_ids must all exist in the pool

SOLVABILITY CHECKS (_verify_task_solvable):
  - Constraints must not eliminate all venues from the pool
  - After universal (agg="all") constraints: ≥2 food and ≥1 site venues must survive per day
  - Scoped/inclusion constraints: ≥1 qualifying venue must exist
  - Type 3: filtered pool must be ≤25 venues
  - Upper bars (must eliminate SOME venues to create selection pressure):
      type1: food ≤75%, site ≤75%, inclusion ≤50%
      type3/6: food ≤50%, site ≤50%, inclusion ≤25%
      type5: food ≤25%, site ≤25%, inclusion ≤10%
  - Type 2: time_ceiling_minutes validated against nearest-K schedule geometry
  - Type 4: budget_per_day validated against cheapest viable plan

UNIVERSAL vs SCOPED CONSTRAINTS — CRITICAL FOR THE UPPER BAR:
  A constraint is a UNIVERSAL POOL FILTER (counts toward the upper bar) ONLY when:
    scope = "all"  AND  aggregation = "all" or "none"
  These remove venues from the shared pool that all other constraints operate on.

  A constraint with scope="category=museum", scope="activity_type=visit",
  scope="has_tag=art", or any non-"all" scope is a SCOPED constraint. It narrows
  which venues qualify for THAT constraint only — it does NOT reduce the food/site
  counts the upper bar measures.

  COMMON MISTAKE: adding scope="category=museum" + agg="all" to fix a site upper
  bar failure. query_pool shows 13 museums — but the validator still sees 28 sites
  because the museum constraint is scoped, not universal.

  TO FIX AN UPPER BAR FAILURE: add scope="all" + agg="all"/"none" + a condition
  that genuinely targets the offending group (district, traffic_tier, tag, field).

  IF DAYS ARE TOO HIGH: the food lower bar (≥2 food per day) can leave too little
  room to also satisfy the site upper bar. Reducing days is often the cleanest fix —
  5+ days on a small post-filter pool is very constrained.

COUNTING CONSTRAINT TENSION (type 3 alternative)
Counting aggregations (at_least, at_most, ratio, count_distinct, at_most_distinct)
constrain the plan space — how many valid schedules exist — rather than filtering
the venue pool. Two counting constraints create type 3 tension when their combined
valid-plan fraction is ≤5% of the baseline plan count. The validator checks this
automatically. Good tension pairs (pull in different directions):
  {at_most_distinct:2, field:district} + {count_distinct:3, field:category}
  {at_least:3, scope:category=museum}  + {at_most_distinct:2, field:district}
  {ratio:0.6, scope:activity_type=meal} + {at_least_days:2}
Bad pairs (same direction — both restrict meals the same way):
  {at_least:2, has_tag:vegan-options} + {at_least:1, has_tag:halal}

If SUBMIT returns errors, read them carefully and fix the specific issues."""

    return handbook


def _query_handbook(query: str, handbook: dict[str, str]) -> str:
    """Dispatch HELP queries to the appropriate handbook section."""
    q = query.lower().strip().lstrip("-h").strip()
    # Direct section lookup
    if "tool" in q:             return handbook.get("tools", "No tools section.")
    if "pool" in q or "field" in q or "filter" in q:
        return handbook.get("pool_fields", "No pool_fields section.")
    if "tag" in q:              return handbook.get("tags", "No tags section.")
    if "regulation" in q:       return handbook.get("regulations", "No regulations section.")
    if "venue_sample" in q or "sample" in q or "get_venue" in q:
        return handbook.get("venue_sample", "No venue_sample section.")
    if "window" in q or "anchor" in q:
        return handbook.get("window", "No window section.")
    if "valid" in q or "submit" in q or "schema" in q:
        return handbook.get("validate", "No validate section.")
    # Default: show all section names
    return (
        f"Handbook sections: {', '.join(handbook.keys())}\n"
        f"Query with '-h <section_name>' e.g. '-h tools', '-h tags', '-h pool_fields'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_task_tool(
    tool_name: str,
    tool_input: dict,
    pool: list[dict],
    pool_map: dict,
    window: dict,
    city: str,
    handbook: dict,
    unavailable: dict,
    type_key: str = "",
    _used_venue_ids: set = None,
    _used_tension_axes: set = None,
    model: str = "",
) -> tuple[dict, bool]:
    """
    Dispatch a tool call.
    Returns (result_dict, should_terminate).
    should_terminate=True only when SUBMIT passes validation.
    """
    if tool_name == "HELP":
        query = tool_input.get("query", "")
        content = _query_handbook(query, handbook)
        return {"status": "ok", "content": content}, False

    elif tool_name == "THINK":
        thought = tool_input.get("thought", "")
        return {"status": "ok", "logged": thought[:200] + "..." if len(thought) > 200 else thought}, False

    elif tool_name == "query_pool":
        # Some models (notably gpt-5.4) forget to wrap filters in a "filters"
        # dict and pass filter keys flat at the top level:
        #   BAD :  {"tag": "hidden-gem", "include": ["coords"]}
        #   GOOD:  {"filters": {"tag": "hidden-gem"}, "include": ["coords"]}
        # Accept both: if "filters" is present use it; otherwise, treat every
        # top-level key except "include" as a filter.
        _filter_keys = {"category", "tag", "regulation", "district",
                        "traffic_tier", "price_tier", "booking_required"}
        raw_filters = tool_input.get("filters")

        # Accept three call styles:
        #   1. Correct:     {"filters": {"tag": "art"}}          -> dict
        #   2. JSON string: {"filters": "{"tag": "art"}"}    -> parse it
        #      Some models JSON-encode the inner dict. Also handle trailing }}
        #      artefacts from format-string construction (e.g. "...art"}}").
        #   3. Flat-style:  {"tag": "art"}                       -> hoist up
        if isinstance(raw_filters, dict):
            filters = raw_filters
        elif isinstance(raw_filters, str) and raw_filters.strip():
            import json as _json
            s = raw_filters.strip()
            # Strip extra trailing braces  (common: "...art"}}" -> "...art"}")
            while s.endswith("}}"):
                s = s[:-1]
            try:
                parsed = _json.loads(s)
                filters = parsed if isinstance(parsed, dict) else {}
            except Exception:
                filters = {}
        else:
            # Flat-style fallback — extract known filter keys from tool_input
            filters = {k: v for k, v in tool_input.items()
                       if k in _filter_keys}
        show_venues = bool(tool_input.get("show_venues", False))
        include = tool_input.get("include", []) if show_venues else []
        results = _query_pool(filters, pool, include=include)

        # Food/site breakdown — the solvability upper bars operate on food and
        # site venues separately, so show the split on every response so agents
        # can calibrate constraints without burning SUBMIT attempts.
        _FOOD_CATS = {"restaurant", "cafe", "bar"}
        _SITE_CATS = {"museum", "attraction", "park", "neighbourhood"}
        n_food_total = sum(1 for v in pool if v.get("category") in _FOOD_CATS)
        n_site_total = sum(1 for v in pool if v.get("category") in _SITE_CATS)
        result_ids   = {r["venue_id"] for r in results}
        res_food = sum(1 for v in pool
                       if v["venue_id"] in result_ids and v.get("category") in _FOOD_CATS)
        res_site = sum(1 for v in pool
                       if v["venue_id"] in result_ids and v.get("category") in _SITE_CATS)
        def _pct(n, d): return f"{100*n//d}%" if d > 0 else "n/a"
        breakdown = (
            f"food={res_food}/{n_food_total} ({_pct(res_food, n_food_total)}), "
            f"site={res_site}/{n_site_total} ({_pct(res_site, n_site_total)})"
        )

        # Type-aware upper-bar targets for the note
        _is_type5 = "type5" in type_key
        _is_type1 = "type1" in type_key
        if _is_type5:
            _univ_pct, _univ_food, _univ_site = "25%", n_food_total // 4, n_site_total // 4
        elif _is_type1:
            _univ_pct, _univ_food, _univ_site = "75%", int(n_food_total * 0.75), int(n_site_total * 0.75)
        else:
            _univ_pct, _univ_food, _univ_site = "50%", n_food_total // 2, n_site_total // 2

        note = (
            f"Breakdown: {breakdown}. "
            f"Targets: ≤{_univ_food} food, ≤{_univ_site} site. "
            f"Use get_venue(venue_id) for full detail."
        )
        if include:
            note = f"Included fields: {list(include)}. {note}"
        if "filters" not in tool_input and filters:
            note += (" (Note: your call was flat-style — please wrap filter "
                     "keys inside 'filters': {...} per the schema.)")
        elif isinstance(tool_input.get("filters"), str):
            note += (" (Note: 'filters' was a JSON string — pass a dict directly: "
                     "filters={...} not filters='{...}'. Results were parsed "
                     "and applied but the correct form is a plain object.)")
        # Venue list is opt-in (show_venues=True).
        # Default: counts only — keeps context lean across many query_pool calls.
        # When show_venues=True and results >30: lite mode (name+ID only).
        _LITE_THRESHOLD = 30
        if not show_venues:
            venue_payload = []
            note += " Pass show_venues=true to see the venue list."
        elif len(results) > _LITE_THRESHOLD:
            # Lite mode: strip include= extras, name+ID only
            venue_payload = [{"venue_id": v["venue_id"], "name": v["name"]}
                             for v in results]
            note += (
                f" {len(results)} venues matched — lite mode (name+ID only, "
                f">{_LITE_THRESHOLD} results). Use a more selective filter + "
                f"include= for extra fields."
            )
        else:
            venue_payload = results
        return {
            "status": "ok",
            "count": len(results),
            "food_count": res_food,
            "site_count": res_site,
            "venues": venue_payload,
            "note": note,
        }, False

    elif tool_name == "get_venue":
        venue_id = tool_input.get("venue_id", "")
        detail = _get_venue_detail(venue_id, pool_map, unavailable)
        if detail is None:
            return {"status": "error", "message": f"venue_id '{venue_id}' not found in pool."}, False
        return {"status": "ok", "venue": detail}, False

    elif tool_name == "estimate_travel":
        id_a = tool_input.get("venue_id_a", "")
        id_b = tool_input.get("venue_id_b", "")
        mode = tool_input.get("mode", "transit")
        va = pool_map.get(id_a)
        vb = pool_map.get(id_b)
        if va is None:
            return {"status": "error", "message": f"venue_id_a '{id_a}' not found."}, False
        if vb is None:
            return {"status": "error", "message": f"venue_id_b '{id_b}' not found."}, False
        lat_a, lng_a = va.get("lat"), va.get("lng")
        lat_b, lng_b = vb.get("lat"), vb.get("lng")
        if lat_a is None or lat_b is None:
            return {
                "status": "error",
                "message": f"One or both venues have no coordinates — cannot estimate travel."
            }, False
        dist_km = haversine_km(lat_a, lng_a, lat_b, lng_b)
        road_km = dist_km * 1.3  # road correction factor
        walk_min    = estimate_minutes(road_km, 5.0)
        transit_min = estimate_minutes(road_km, 20.0) + 5  # +5 min overhead
        cycle_min   = estimate_minutes(road_km, 15.0)
        result_min  = {"walking": walk_min, "transit": transit_min, "cycling": cycle_min}.get(mode, transit_min)
        return {
            "status":       "ok",
            "venue_a":      va.get("name"),
            "venue_b":      vb.get("name"),
            "distance_km":  round(dist_km, 2),
            "walk_minutes": round(walk_min, 1),
            "transit_minutes": round(transit_min, 1),
            "cycling_minutes": round(cycle_min, 1),
            "requested_mode": mode,
            "result_minutes": round(result_min, 1),
            "note": "Straight-line × 1.3 road factor. Real travel 10-30% longer — build buffer."
        }, False

    elif tool_name == "get_official_site":
        venue_id = tool_input.get("venue_id", "")
        date_str  = tool_input.get("date", "")
        if not venue_id:
            return {"status": "error", "message": "venue_id is required."}, False
        if not date_str:
            return {"status": "error", "message": "date is required (YYYY-MM-DD)."}, False

        # Look up venue to confirm it exists and has an official site
        venue_detail = _get_venue_detail(venue_id, pool_map, unavailable)
        if venue_detail is None:
            return {"status": "error", "message": f"venue_id '{venue_id}' not found in pool."}, False
        if not venue_detail.get("has_official_site"):
            return {
                "status": "no_official_site",
                "venue": venue_detail.get("name"),
                "message": "This venue has no official site in the database."
            }, False

        # Fetch official site doc from DB
        try:
            from scripts.generation.db import get_city_db_path
            _db_path = get_city_db_path(city)
            _conn = get_connection(_db_path)
            row = _conn.execute(
                "SELECT body, active_event, ticket_availability FROM official_site_docs "
                "WHERE venue_id = ? AND page_status = 'verified' LIMIT 1",
                (venue_id,)
            ).fetchone()
            # Also check events table for this venue on this date
            event_rows = _conn.execute(
                "SELECT name, description, affects_hours, affects_access, sold_out, modified_hours "
                "FROM events WHERE venue_id = ? AND start_date <= ? AND end_date >= ?",
                (venue_id, date_str, date_str)
            ).fetchall()
            # Check ticket_availability for this date
            avail_row = _conn.execute(
                "SELECT status, slots_remaining, notes FROM ticket_availability "
                "WHERE venue_id = ? AND date = ?",
                (venue_id, date_str)
            ).fetchone()
            _conn.close()
        except Exception as e:
            err_str = str(e)
            if "no such table" in err_str:
                return {
                    "status":  "not_available",
                    "message": (
                        "Official site docs are not available for this city/window yet "
                        "(source documents not yet generated). "
                        "Use get_venue() for venue details instead. "
                        "Do not retry get_official_site — it will not succeed."
                    )
                }, False
            return {"status": "error", "message": f"DB lookup failed: {e}"}, False

        result = {
            "status": "ok",
            "venue_id": venue_id,
            "venue_name": venue_detail.get("name"),
            "date": date_str,
        }

        if row:
            result["page_body"] = row[0] or "(no body text)"
        else:
            result["page_body"] = "(no official site content indexed)"

        # Active events on this date
        if event_rows:
            ecols = ["name", "description", "affects_hours", "affects_access", "sold_out", "modified_hours"]
            result["events_on_date"] = [dict(zip(ecols, r)) for r in event_rows]
        else:
            result["events_on_date"] = []

        # Ticket availability
        if avail_row:
            result["ticket_availability"] = {
                "status": avail_row[0],
                "slots_remaining": avail_row[1],
                "notes": avail_row[2],
            }
        else:
            result["ticket_availability"] = {"status": "unknown", "slots_remaining": None}

        if result["events_on_date"] or (avail_row and avail_row[0] in ("sold_out", "limited")):
            result["note"] = (
                "This venue has active events or limited availability on this date. "
                "A solving agent must check this before booking."
            )

        return result, False

    elif tool_name == "SUBMIT":
        task_json_str = tool_input.get("task_json", "")
        # 1. Parse JSON
        try:
            task = json.loads(task_json_str) if isinstance(task_json_str, str) else task_json_str
        except json.JSONDecodeError as e:
            return {
                "status": "validation_failed",
                "errors": [f"JSON parse error: {e}"],
                "warnings": [],
                "message": "Fix the JSON syntax and try SUBMIT again."
            }, False

        # 1a. Auto-correct window_id to match the actual window config
        _correct_wid = window.get("window_id", "")
        if _correct_wid and task.get("window_id") != _correct_wid:
            task["window_id"] = _correct_wid

        # 1a3. Auto-populate query_resources.currency from the city (P6-T12).
        # Many type4 tasks ship with currency=None — fix in-place so downstream
        # rendering and future evaluator checks have a value.
        from scripts.generation.pool_utils import get_city_currency
        _pi = task.setdefault("public_input", {})
        if isinstance(_pi, dict):
            _qr = _pi.setdefault("query_resources", {})
            if isinstance(_qr, dict) and not _qr.get("currency"):
                _qr["currency"] = get_city_currency(city)

        # 1a2. Auto-generate canonical task_id: {model}_{city}_{type}_{unix_ts}
        # Ignore whatever the agent wrote — ensures consistent, sortable filenames.
        import re as _re, time as _time
        _type_short = _re.search(r"type[0-9]+", type_key)
        _type_short = _type_short.group(0) if _type_short else type_key or "task"
        _model_slug = _re.sub(r"[^a-z0-9]+", "_",
                               (model or "unknown").lower()).strip("_")[:22]
        _model_slug = _re.sub(r"_+", "_", _model_slug).rstrip("_")
        import datetime as _dt
        _ts   = int(_time.time())
        _date = _dt.datetime.now().strftime("%Y%m%d")
        task["task_id"] = f"{_model_slug}_{city}_{_type_short}_{_date}_{_ts}"

        # 1b. Enforce difficulty enum (only when field is present — 
        #     missing fields are caught by schema validation below)
        _VALID_DIFFICULTIES = {"easy", "medium", "hard"}
        _diff = task.get("difficulty")
        if _diff is not None:
            if isinstance(_diff, str):
                _diff = _diff.strip().lower()
            if _diff not in _VALID_DIFFICULTIES:
                return {
                    "status": "validation_failed",
                    "errors": [
                        f"difficulty must be one of {sorted(_VALID_DIFFICULTIES)}, "
                        f"got '{task.get('difficulty', '')}'. Pick easy, medium, or hard."
                    ],
                    "warnings": [],
                    "message": "Fix difficulty and SUBMIT again."
                }, False
            task["difficulty"] = _diff  # normalise to lowercase

        # 1c. Enforce canonical structural_type (only when field is present)
        _CANONICAL_STRUCTURAL_TYPES = {
            "type1_cascading_requirements",
            "type2_subset_selection",
            "type3_competing_requirements",
            "type4_precision_allocation",
            "type5_hard_feasibility_reduction",
            "type6_context_window_tension",
        }
        # Auto-correct common short forms ("type1", "1", etc.) — P6-T3.
        _STRUCTURAL_TYPE_ALIASES = {
            "type1": "type1_cascading_requirements",
            "type2": "type2_subset_selection",
            "type3": "type3_competing_requirements",
            "type4": "type4_precision_allocation",
            "type5": "type5_hard_feasibility_reduction",
            "type6": "type6_context_window_tension",
            "1": "type1_cascading_requirements",
            "2": "type2_subset_selection",
            "3": "type3_competing_requirements",
            "4": "type4_precision_allocation",
            "5": "type5_hard_feasibility_reduction",
            "6": "type6_context_window_tension",
        }
        _st = task.get("structural_type")
        if isinstance(_st, str) and _st in _STRUCTURAL_TYPE_ALIASES:
            task["structural_type"] = _STRUCTURAL_TYPE_ALIASES[_st]
            _st = task["structural_type"]
        if _st is not None and _st not in _CANONICAL_STRUCTURAL_TYPES:
            return {
                "status": "validation_failed",
                "errors": [
                    f"structural_type must be one of: "
                    f"{', '.join(sorted(_CANONICAL_STRUCTURAL_TYPES))}. "
                    f"Got '{_st}'."
                ],
                "warnings": [],
                "message": "Fix structural_type and SUBMIT again."
            }, False

        # 1d. Type 6 venue diversity — reject repeated required_venue_ids
        _rvids = task.get("rubric", {}).get("required_venue_ids", [])
        _pc_vids = [
            c["scope"].split("=")[1]
            for c in task.get("rubric", {}).get("personal_constraints", [])
            if isinstance(c.get("scope"), str) and c["scope"].startswith("venue_id=")
        ]
        _all_task_vids = set(_rvids) | set(_pc_vids)
        _reused = _all_task_vids & (_used_venue_ids or set())
        if _reused:
            return {
                "status": "validation_failed",
                "errors": [
                    f"required_venue_id(s) {_reused} already used in a previous task "
                    f"this run. Choose different venues to ensure benchmark diversity."
                ],
                "warnings": [],
                "message": "Pick different venues and SUBMIT again."
            }, False

        # 1e. V2: Type 3 tension axis diversity — reject reused tension pairs
        if "type3" in str(task.get("structural_type", "")):
            _task_axes = set()
            for _pc in task.get("rubric", {}).get("personal_constraints", []):
                _cond = _pc.get("condition", {})
                if isinstance(_cond, dict):
                    _axis = _cond.get("field") or _cond.get("has_tag") or _cond.get("not_tag")
                    if _axis:
                        _task_axes.add(_axis)
            _reused_axes = _task_axes & (_used_tension_axes or set())
            if _reused_axes:
                return {
                    "status": "validation_failed",
                    "errors": [
                        f"Type 3 tension axis/axes {_reused_axes} already used in a "
                        f"previous Type 3 task this run. Use a different tension pair "
                        f"— see the tension patterns in the Type 3 protocol: "
                        f"tag-based (outdoor vs indoor, historic vs contemporary), "
                        f"scoped (meals vs visits), cross-category, or noise/price axes."
                    ],
                    "warnings": [],
                    "message": "Pick a different tension axis and SUBMIT again."
                }, False

        # 2. Schema validation
        try:
            from test_generate_tasks import validate_task_schema
            schema_issues = validate_task_schema(task, pool)
        except ImportError:
            schema_issues = []

        hard_issues  = [i for i in schema_issues if not i.startswith("~ ")]
        soft_warnings = [i for i in schema_issues if i.startswith("~ ")]

        # 3. Solvability check
        try:
            from scripts.generation.generate_task import _verify_task_solvable
            solvable, solvability_reason = _verify_task_solvable(task, pool)
        except Exception as e:
            solvable, solvability_reason = True, f"solvability check skipped: {e}"

        all_errors = list(hard_issues)
        if not solvable:
            all_errors.append(f"Solvability: {solvability_reason}")

        if all_errors:
            return {
                "status":   "validation_failed",
                "errors":   all_errors,
                "warnings": soft_warnings,
                "message":  (
                    f"{len(all_errors)} issue(s) to fix. "
                    f"Use THINK to plan your fixes, then call SUBMIT again with the corrected JSON. "
                    f"Use '-h validate' to understand what each check requires."
                )
            }, False

        # Validation passed
        task["_validation_warnings"] = soft_warnings
        return {
            "status":   "accepted",
            "task_id":  task.get("task_id", "?"),
            "warnings": soft_warnings,
            "message":  "Task accepted. Loop will terminate.",
            "_accepted_task": task,  # corrected task (window_id, difficulty normalised)
        }, True  # ← terminate

    else:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}, False


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _build_agent_system_prompt(
    type_key: str,
    city: str,
    window: dict,
    pool: list[dict],
    centre_lat: float,
    centre_lng: float,
    unavailable: dict,
) -> str:
    """Build the system prompt for the task generation agent."""
    import math as _math
    lng_factor = round(_math.cos(_math.radians(centre_lat)) * 111, 1)

    dates      = window.get("dates", [])
    date_range = f"{dates[0]} – {dates[-1]}" if dates else "?"
    anchor_str = ", ".join(
        f"{e['name']} ({e['date']})" for e in window.get("anchor_events", [])
    )

    # Count pool stats for context
    cat_counts: dict[str, int] = {}
    n_with_coords = 0
    n_sold_out_venues = len(unavailable)
    for v in pool:
        cat_counts[v.get("category", "?")] = cat_counts.get(v.get("category", "?"), 0) + 1
        if v.get("lat") and v.get("lng"):
            n_with_coords += 1

    # ── Dynamic pool stats for type4 and type5 ──────────────────────────────
    FOOD_CATS = {"restaurant", "cafe", "bar"}
    SITE_CATS = {"museum", "attraction", "park", "neighbourhood"}

    # PRE-5: Type 4 cheapest plan reference
    food_sorted = sorted(
        [v for v in pool if v.get("category") in FOOD_CATS],
        key=lambda v: v.get("avg_cost_local") or 999
    )
    site_sorted = sorted(
        [v for v in pool if v.get("category") in SITE_CATS],
        key=lambda v: v.get("avg_cost_local") or 999
    )
    cheapest_day = (
        sum(v.get("avg_cost_local", 0) for v in food_sorted[:2]) +
        sum(v.get("avg_cost_local", 0) for v in site_sorted[:2])
    )
    # Try to get currency symbol from city config or fall back to generic
    _city_currency = {
        "london": "£", "paris": "€", "tokyo": "¥", "istanbul": "₺",
        "rio": "R$", "hokkaido": "¥", "barcelona": "€", "new york": "$",
    }.get(city.lower(), "$")
    budget_floor_str  = f"{_city_currency}{cheapest_day * 1.1:.0f}"
    budget_ceil_str   = f"{_city_currency}{cheapest_day * 1.3:.0f}"
    cheapest_day_str  = f"{_city_currency}{cheapest_day:.0f}"

    # PRE-4: Type 5 per-filter survival stats
    PRICE_ORDER = ["free", "budget", "mid", "upscale", "luxury"]
    def _price_gte(pool_list, tier):
        threshold = PRICE_ORDER.index(tier) if tier in PRICE_ORDER else 0
        return [v for v in pool_list if PRICE_ORDER.index(v.get("price_tier","mid") if v.get("price_tier","mid") in PRICE_ORDER else "mid") >= threshold]

    def _count_filter_single(v, filters_dict):
        """Return True if venue v passes all conditions in filters_dict."""
        for k, val in filters_dict.items():
            if k == "tag":
                if val not in v.get("tags", []):
                    return False
            elif k == "regulation":
                regs = v.get("regulations", {}) or {}
                if not regs.get(val, v.get(val)):
                    return False
            elif k == "traffic_tier":
                if v.get("traffic_tier") != val:
                    return False
            elif k == "price_tier_gte":
                t = v.get("price_tier", "mid")
                if t not in PRICE_ORDER:
                    return False
                if PRICE_ORDER.index(t) < PRICE_ORDER.index(val):
                    return False
            elif k == "district":
                if v.get("district") != val:
                    return False
            elif k == "booking_required":
                if bool(v.get("booking_required", 0)) != bool(val):
                    return False
        return True

    def _count_filter(filters_dict):
        return sum(1 for v in pool if _count_filter_single(v, filters_dict))

    # Build candidate filter list from actual pool data
    _candidate_filters = [
        ("traffic_tier=low",           {"traffic_tier": "low"}),
        ("traffic_tier=mid",           {"traffic_tier": "mid"}),
        ("price_tier>=upscale",        {"price_tier_gte": "upscale"}),
        ("regulation=wheelchair_accessible", {"regulation": "wheelchair_accessible"}),
        ("regulation=pet_friendly",    {"regulation": "pet_friendly"}),
        ("regulation=reservation_required", {"regulation": "reservation_required"}),
        ("booking_required=true",      {"booking_required": True}),
    ]
    # Add top tags by frequency (those appearing on 5–20% of pool = most useful filters)
    from collections import Counter as _Counter
    tag_freq = _Counter(t for v in pool for t in v.get("tags", []))
    useful_tags = [t for t, n in tag_freq.most_common(30)
                   if 3 <= n <= len(pool) * 0.4][:8]
    for t in useful_tags:
        _candidate_filters.append((f"tag={t}", {"tag": t}))
    # Add top districts
    dist_freq = _Counter(v.get("district","") for v in pool if v.get("district"))
    for dist, _ in dist_freq.most_common(4):
        _candidate_filters.append((f"district={dist}", {"district": dist}))

    stats_lines = []
    n_food_pool = sum(1 for v in pool if v.get("category") in FOOD_CATS)
    n_site_pool = sum(1 for v in pool if v.get("category") in SITE_CATS)
    for label, filt in _candidate_filters:
        n_food = sum(1 for v in pool
                     if v.get("category") in FOOD_CATS and _count_filter_single(v, filt))
        n_site = sum(1 for v in pool
                     if v.get("category") in SITE_CATS and _count_filter_single(v, filt))
        pct_food = 100 * n_food // max(1, n_food_pool)
        pct_site = 100 * n_site // max(1, n_site_pool)
        bar_food = "▓" if pct_food <= 15 else ("░" if pct_food <= 35 else " ")
        bar_site = "▓" if pct_site <= 15 else ("░" if pct_site <= 35 else " ")
        stats_lines.append(
            f"  {label:<38}  food {bar_food}{n_food:>2}/{n_food_pool}({pct_food:>2}%)"
            f"  site {bar_site}{n_site:>2}/{n_site_pool}({pct_site:>2}%)"
        )
    pool_filter_stats = "\n".join(stats_lines)

    _type5_protocol = f"""REASONING PROTOCOL — TYPE 5 (Hard Feasibility Reduction):

TYPE 5 IS ABOUT STACKING NARROW FILTERS. The goal: after applying your
constraints, only a handful of viable venues per meal/site slot remain.
This is extreme narrowing — harder than type 3, and intentionally so.

⚠ SCOPE RULES (type 5 makes these even more critical):
A constraint with scope="all" + aggregation="all" is a POOL FILTER that removes
every non-matching venue from ALL categories. If your condition uses a tag that
only exists on food venues (e.g. has_tag:"halal"), you will eliminate all museums
and parks. Safe patterns:
  scope="all" + agg="all"         → only use with fields that apply to ALL venues:
                                    regulations (wheelchair_accessible, noise_level),
                                    traffic_tier, price_tier, district
  scope="activity_type=meal" + agg="all" → filters only food venues (safe)
  scope="category=museum" + agg="all"   → filters only museums (safe)

POOL FILTER STATISTICS (▓=tight ≤15%, ░=medium 16-35%, ' '=loose >35%):
  Columns: food survivors / total food (%), site survivors / total site (%)
  Target for type5: food ≤25% AND site ≤25% after ALL combined filters.
{pool_filter_stats}
  Stack 2-3 filters to reach ≤25% on BOTH food and site.
  Zero survivors on either group = unsolvable. Both >25% = not tight enough.
  Different tasks should use DIFFERENT combinations — don't always use the same pair.

VALID TYPE 5 COMBINATIONS:
  regulation + traffic_tier:    wheelchair_accessible AND traffic_tier=low
  regulation + tag (scoped):    wheelchair_accessible AND scope=meal has_tag=halal
  tag + district:               has_tag=hidden-gem AND district=Shoreditch
  two regulations:              wheelchair_accessible AND pet_friendly
  price + traffic:              price_tier>=upscale AND traffic_tier=low

STEP BY STEP:
1. THINK: Pick 2-3 filters from the stats table above. Prefer combinations not
   used by other type 5 tasks in this run. Mix categories for genuine depth.
2. Use query_pool progressively — stack your chosen filters in one call
   and read the food/site breakdown in the response. Target: food ≤25% AND
   site ≤25% (exact ceiling numbers are shown in the table above).
3. Check both groups have ≥1 survivor each. If either drops to 0: loosen
   one filter. If both groups are >25%: add another filter.
4. Submit with constraints that match the filters you validated."""

    # PRE-5: Inject cheapest plan cost into type4 protocol
    _type4_protocol = f"""REASONING PROTOCOL — TYPE 4 (Precision Allocation):
Type 4 tests whether the agent can allocate a tight budget wisely. The
binding resource (budget) lives in the NATURAL-LANGUAGE query as a specific
number AND must be carried structurally in
public_input.query_resources.budget_per_day (in the city's LOCAL CURRENCY —
do not convert to USD). The validator enforces a 1.1–1.3× ratio band against
the cheapest-viable-plan cost (2 restaurants + 2 sites per day, cheapest
from the filtered pool, plus premium add for any required_venue_ids that
cost > 1.5× their category median).

POOL COST REFERENCE ({city.title()}):
  Cheapest 2 food + 2 site venues/day ≈ {cheapest_day_str}
  Your budget_per_day must be in range: {budget_floor_str}–{budget_ceil_str}
  (1.1–1.3× cheapest viable plan — validator enforces this band)
  Too low (< {budget_floor_str}) = infeasible, SUBMIT will reject it.
  Too high (> {budget_ceil_str}) = no pressure, SUBMIT will reject it.

BUDGET PRESSURE ARCHETYPES — pick ONE and be creative:
   • Celebration splurge: tight daily budget but must fit ≥1 fine-dining meal
   • Challenge persona: every single venue must cost ≤ £N (YouTuber budget
     challenge, backpacker, "£10-a-day London") — use all+condition{{avg_cost_local<=N}}
   • Premium requirement: all meals must be upscale (business expense account)
     so the cap forces wise selection, not downgrading
   • Free-entry maximiser: budget is very tight so must use only free-entry
     sites — use all+condition{{free_entry=True}} for visits
   • Occasion + exploration: one expensive fixed venue (theatre ticket,
     Michelin restaurant) + tight budget for everything else

   DO NOT default to "birthday dinner" every time. The archetype creates the
   story. The budget creates the constraint. Think of different scenarios.

0. THINK: Target budget_per_day in {budget_floor_str}–{budget_ceil_str}.
   This is the valid range BEFORE additional constraints. Adding P-constraints
   that require expensive venues (e.g. all meals upscale) raises the floor
   — verify with query_pool include=["price"] before committing to a number.
1. Use query_pool with include=["price"] to fetch actual venue costs.
   Sort by avg_cost_local to check the cheapest viable plan for your
   specific filtered pool (may be higher than {cheapest_day_str} if you
   exclude cheap venues).
2. State the budget per day in the query with local currency ({_city_currency}).
   The persona's reason for the budget shapes WHICH archetype you chose, not
   just why they have one special meal.
3. Put a required expensive venue in rubric.required_venue_ids if the persona
   names a specific place; its cost adds to the floor if >1.5× category median.
4. P-constraints MUST include a sum aggregation budget constraint:
     • scope:"per_day" condition:{{}} agg:{{"sum":"estimated_cost_local","operator":"<=","value":<budget>}}
     This makes allocation pressure scoreable.
   Add 1-2 more P-constraints shaped by the chosen archetype:
     Challenge: all scope=all condition={{avg_cost_local<=N}}
     Premium:   all scope=activity_type=meal condition={{price_tier>="upscale"}}
     Free-entry: all scope=activity_type=visit condition={{has_tag:"free-entry"}}
     Occasion:  at_least scope=venue_id=X (the specific expensive venue)
5. Verify: cheapest plan with your constraints + 1.1× ≤ your budget ≤ 1.3×.
   If outside the band, adjust budget or loosen one filter and re-check."""

    # Per-type reasoning protocol
    type_protocols = {
        "type1": """REASONING PROTOCOL — TYPE 1 (Cascading Requirements):
Type 1's difficulty is INFERENCE: the solving agent must derive multiple
constraints from a rich natural-language persona description. The constraints
should make the plan better, not harder to satisfy — no opposing requirements.

1. THINK: Design a persona with a clear composition (who they are: profession,
   interests, travel companion) and a specific occasion or purpose for the trip.
   A rich persona naturally produces 3-5 constraint signals in the query.
2. For each signal in the query, derive the constraint it implies. At least one
   must require reasoning beyond label-reading (hop-2): e.g. "anniversary dinner"
   → upscale restaurant; "travelling with elderly parent" → wheelchair accessible.
   Every constraint must trace to something explicitly stated in the query.
3. Use query_pool to verify two things before SUBMIT:
   a) LOWER bar — enough venues survive to fill a schedule (2 food + 1 site per day).
   b) UPPER bar — your constraints must do SOMETHING to the pool. Type 1 needs
      ≤75% of food venues and ≤75% of site venues to survive after all constraints
      (looser than type3 because you're describing a believable traveller, not
      creating competitive pressure). Stack your universal (scope="all") constraint
      filters in one query_pool call and check the food/site counts. If either group
      is still >75%, add one more scope="all" constraint targeting that group.
      IMPORTANT: only scope="all" constraints reduce the universal pool counts.
      scope="category=museum" or scope="activity_type=visit" constraints are scoped —
      they do NOT move the upper bar needle. If you're stuck on the site upper bar
      after adding a museum-only constraint, that's why.
      If days is large (5+) and the food count is tight, reducing days may be easier
      than finding a compatible universal filter.
4. Constraints don't need to oppose each other — they just collectively describe
   a believable traveller with coherent, real needs.""",

        "type2": """REASONING PROTOCOL — TYPE 2 (Subset Selection Under Time Ceiling):
Type 2 tests whether the solving agent can select a good subset of venues
within a stated time budget. The time ceiling must appear numerically in the
query AND in public_input.query_resources.time_ceiling_minutes.

TWO MODES — pick one and set ceiling_mode accordingly:
  • contiguous (DEFAULT): a single block of time on a specific day.
      "I have Saturday afternoon 14:00–18:00 free" → 240 min block.
      REQUIRES: query_resources.start_time = "HH:MM"
      Evaluator: every activity must fall in [start_time, start_time+ceiling].
  • spread: a total activity budget that doesn't have to be contiguous.
      "6 hours of activities spread across the 2-day trip"
      → ceiling=360 (and ceiling_scope defaults to 'total').
      Evaluator: sum across days vs ceiling (or per_day with ceiling_scope=per_day).
      MEAL FLOOR: spread does NOT impose a 2-meals/day floor. If the persona
      needs a meal, express it via a P-constraint — don't assume meals
      structurally. The traveller may eat at home/hotel between activities.

query_resources fields for type2:
  time_ceiling_minutes  int    REQUIRED — numeric budget
  ceiling_mode          str    "contiguous" (default) | "spread"
  ceiling_scope         str    "total" (default) | "per_day"
                                  (only meaningful for spread on multi-day trips)
  start_time            str    "HH:MM" — REQUIRED when ceiling_mode=contiguous;
                                  omit otherwise

0. THINK: What time ceiling does the user's query imply, and which mode? The
   validator checks it against a minimum viable schedule: K nearest venues by
   centroid distance, each counted at half their recommended_visit_minutes plus
   nearest-neighbour travel. K = max(largest at_least aggregation, days × 2).
   For at_least with scope='per_day', K uses N × days. Prefer scope='all' or
   scope='category=X' for the global floor.
   Your ceiling must be 1.1–1.3× that minimum. Single-day cap: 600 min (10 hrs).
   For ceiling_scope=per_day, the effective total used by the validator is
   ceiling × days.
1. THINK: Decide the venue subgroup (category + any tag/tier filter).
2. Use query_pool with include=["duration","coords"] to fetch candidates in bulk.
3. Compute the minimum K-venue schedule: sum(0.5 × recommended_visit_minutes
   for the K nearest venues by centroid) + nearest-neighbour travel between them.
4. Set time_ceiling_minutes to 1.1–1.3× that minimum. Set ceiling_mode + (if
   contiguous) start_time + (if multi-day spread) ceiling_scope.
5. State the ceiling, mode, and your calculation in THINK before SUBMIT.
Travel formula: dist_km ≈ sqrt((Δlat×111)² + (Δlng×{lng_factor})²)
  walk=dist×12min  transit=dist×8+5min  (or use estimate_travel)""",

        "type3": """REASONING PROTOCOL — TYPE 3 (Competing Requirements):

WHAT TYPE 3 IS: Two INCLUSION constraints that compete for limited schedule
slots. The solving agent must BALANCE two preferences that pull at different
parts of the pool. Neither can dominate — both must be substantially present.

⚠ CRITICAL — USE INCLUSION CONSTRAINTS, NOT POOL FILTERS:
Type 3 tension comes from competing COUNTS in a limited schedule, not from
narrowing the pool. Use aggregation={at_least: N}, NOT aggregation="all".

  ✗ WRONG: agg="all" condition={traffic_tier=="high"}
    → Pool filter — eliminates all non-high venues. If the other constraint
      also filters with an opposing value, the pool becomes empty.

  ✓ RIGHT: agg={at_least: N} condition={traffic_tier=="high"}
    → Inclusion constraint — "include at least N iconic venues in the plan."
      Pool stays intact. Difficulty comes from fitting both sides into
      limited days × ~4 activity slots.

THE FORMULA for N: min(2 × days, matching_venue_count)
  For a 2-day trip: at_least = min(4, matching_count)
  For a 3-day trip: at_least = min(6, matching_count)
  Use query_pool to find matching_count for each side before choosing N.

DESIGNING TYPE 3 TENSION — step by step:
1. THINK: Pick two signals from the query that pull in different directions.
   DO NOT default to high-traffic vs low-traffic every time. Choose from
   these tension patterns — vary across tasks:

   FIELD-BASED TENSIONS (use condition with field/operator/value):
     traffic_tier=high vs traffic_tier=low   — iconic vs hidden gems
     price_tier>=upscale vs price_tier=budget — splurge vs save
     noise_level>=lively vs noise_level<=quiet — buzzy vs peaceful

   TAG-BASED TENSIONS (use condition with has_tag):
     has_tag=history vs has_tag=contemporary — old vs new
     has_tag=hidden-gem vs has_tag=iconic — discovery vs landmark
     has_tag=lively vs has_tag=cosy — buzzing vs intimate
     has_tag=street-food vs has_tag=michelin-star — quick vs refined
     ⚠ Tag-mirror concepts that already live in regulation/price/noise/dress
       columns are NOT in the tag vocab any more (P6-T2-B). For tensions like
       budget-vs-premium, use price_tier ranges; for family-vs-adult, use
       family_friendly / age_restriction; for quiet-vs-loud, use noise_level.
     ⚠ Tags must NOT substantially overlap. Use query_pool to check both sides
       have ≥2×days venues each. The tension group validation also checks that
       overlap ≤25% of the smaller set.

   SCOPED TENSIONS (different scopes pulling at the same schedule):
     scope=activity_type=meal agg={at_least:3} field:"local_cuisine" op:"==" val:1
       vs scope=category=museum agg={at_least:3}
     — foodie wants many local-cuisine meals, but partner wants museum time
     (note: "local cuisine" is the `local_cuisine` integer column, not a tag)

   CROSS-CATEGORY TENSIONS:
     scope=all agg={at_least:4} has_tag=outdoor-seating
       vs scope=activity_type=meal agg={at_least:3} price_tier>=upscale
     — outdoor lover who also demands fine dining (hard to schedule both)

   Use HELP('-h tags') to discover what tags actually exist in this pool.
   Use query_pool to count venues for each side BEFORE committing to a pair.

2. Use query_pool to count each side:
   - query_pool({"filters": {"traffic_tier": "high"}}) → how many high-traffic? (counts only)
   - query_pool({"filters": {"traffic_tier": "low"}}) → how many low-traffic?
   - query_pool({"filters": {"traffic_tier": "low"}, "show_venues": true}) → low-traffic venue list
   Both sides need ≥ 2×days qualifying venues. If a side has fewer, reduce N
   to match, or pick a different pair.

3. Encode as INCLUSION constraints (at_least, NOT "all"):
   EXAMPLE A — field-based (2-day trip):
     pc_001: scope="all"  agg={at_least: 4}
             condition={field:"traffic_tier", op:"==", val:"high"}
             source_in_profile: "my partner insists on the famous sites"
     pc_002: scope="all"  agg={at_least: 4}
             condition={field:"traffic_tier", op:"==", val:"low"}
             source_in_profile: "I prefer hidden gems off the tourist trail"

   EXAMPLE B — tag-based (3-day trip, outdoor lover vs museum buff):
     pc_001: scope="all"  agg={at_least: 5}
             condition={has_tag: "outdoor-seating"}
             source_in_profile: "I want to spend as much time outdoors as possible"
     pc_002: scope="category=museum"  agg={at_least: 4}
             condition={}
             source_in_profile: "my partner wants to visit every major museum"

   EXAMPLE C — scoped cross-category (2-day foodie trip):
     pc_001: scope="activity_type=meal"  agg={at_least: 3}
             condition={field: "local_cuisine", op: "==", val: 1}
             source_in_profile: "I want to try authentic local food at every meal"
     pc_002: scope="all"  agg={at_least: 3}
             condition={field: "price_tier", op: "<=", val: "budget"}
             source_in_profile: "we're on a tight budget"
     → Tension: local restaurants tend to be mid-priced, budget venues are chains

4. If validation says "No constraint tension detected" after a valid pair,
   the B×B joint probability may be above 10%. To tighten:
   a) Increase at_least values (e.g. 4 → 5) if the pool supports it
   b) Add a third constraint aligned with the persona that cross-cuts both
      sides — e.g. {scope="activity_type=meal", agg={at_least:2},
      condition={has_tag:"vegetarian-options"}} if the persona is vegetarian.
      This adds another competing demand on the same schedule slots.
   c) Use scoped constraints to target food vs sites separately.

5. The tension should feel NATURAL to the persona. Both preferences must trace
   to something explicitly in the query. Do not add artificial constraints
   just to pass validation — design a richer persona instead.""",

        "type4": _type4_protocol,

        "type5": _type5_protocol,

        "type6": """REASONING PROTOCOL — TYPE 6 (Context-Window Tension):
1. THINK: Decide the seasonal situation (which anchor event, what it creates:
   closure zone, sold-out date, modified hours, crowd surge).
2. Decide the need: what does the persona want that puts them in tension with
   that situation?
3. THINK: Is this a one-time requirement (one special venue) or whole-schedule?
   One-time: 1-3 venues should satisfy the need while avoiding the conflict.
   Whole-schedule: 1-3 viable venues per day category after conflict is applied.
4. Use query_pool + get_venue to check window_flags and unavailable_dates.
   Use get_official_site(venue_id, date) to check for active events, sold-out
   status, or modified hours on specific anchor dates.
   Confirm enough unaffected venues exist to complete the schedule.
5. Confirm the conflict is discoverable — the solving agent must call
   get_official_site with the anchor date to find it.
6. Ground the tension structurally: use a time_window= scope P-constraint
   (schedule timing tied to window) OR a venue_id= P-constraint on a
   window-affected venue. Without one of these, SUBMIT will fail with
   "type 6 not grounded".""",
    }

    # Get the protocol for this type (strip "type1_cascading_requirements" → "type1")
    type_key_short = type_key.split("_")[0] if "_" in type_key else type_key
    protocol = type_protocols.get(type_key_short, type_protocols.get(type_key, ""))
    if not protocol:
        # Try matching any key that starts with the type_key
        for k, v in type_protocols.items():
            if type_key.startswith(k) or k.startswith(type_key):
                protocol = v
                break

    # Format pool listing for context (compact — agent uses tools for detail)
    pool_lines = []
    for v in pool[:50]:
        lat, lng = v.get("lat"), v.get("lng")
        coord = f"{lat:.3f},{lng:.3f}" if lat and lng else "?,?"
        cost  = f"${v.get('avg_cost_local', 0):.0f}" if v.get("avg_cost_local") else "free"
        sold  = " [SOLD]" if v["venue_id"] in unavailable else ""
        wi    = ""
        if v.get("has_wrong_info"):
            cats = v.get("wrong_info_categories", [])
            cat_str = ":".join(cats) if cats else "?"
            wi = f" [stale:{cat_str}]"
            activations = v.get("wrong_info_activation", [])
            if activations:
                wi += f"[activates:{','.join(activations)}]"
        pool_lines.append(
            f"  {v['venue_id']} | {v['name']} | {v.get('category','')} | "
            f"{v.get('district','')} | {v.get('traffic_tier','')} | "
            f"{v.get('recommended_pace','')} | {cost} | {coord}{sold}{wi}"
        )

    return f"""You are a benchmark task generation agent for TravelBench.

Your job: explore the venue pool using tools, design a valid {type_key} task,
and call SUBMIT with the final task JSON. The loop ends when SUBMIT passes validation.

═══════════════════════════════════════════════════════════════════════════
HOW YOU INTERACT WITH THIS ENVIRONMENT — READ CAREFULLY
═══════════════════════════════════════════════════════════════════════════
This is a TOOL-CALLING environment. You have access to 7 tools:
  HELP, THINK, query_pool, get_venue, get_official_site, estimate_travel, SUBMIT

Every response you write must INVOKE ONE OR MORE TOOLS. Do not write prose
responses that describe what you would do — actually call the tool. In particular:

  ✗ Do NOT write: "I will now submit the task: {{...task JSON...}}"
  ✓ Instead: actually call SUBMIT(task_json="...") as a tool invocation.

  ✗ Do NOT write: "Let me think about this. I would consider..."
  ✓ Instead: call THINK(thought="...") as a tool invocation.

A prose-only response with no tool call is a bug — you will receive a
reminder asking you to use the tool, and the loop will terminate if you
ignore the reminder. The validation system only runs on SUBMIT tool calls.
═══════════════════════════════════════════════════════════════════════════

TURN BUDGET: You have {MAX_TURNS} turns total. Each tool call costs one turn.
Budget your turns like this:
  • Turns 1-8:  EXPLORE — THINK, query_pool, get_venue to understand the pool
  • Turns 9-12: DRAFT — call SUBMIT with your best task JSON (even if you
                think it's not perfect — the validator will tell you what
                to fix, which is more valuable than more exploration)
  • Turns 13+:  FIX — use validation errors to correct the task and SUBMIT
                again. Each SUBMIT failure gives you specific errors to fix.

Do NOT spend 15+ turns exploring before your first SUBMIT. Once you see
validation errors, you can fix them precisely. You cannot fix a task you
haven't drafted yet. If you hit turn {MAX_TURNS - URGENCY_TURNS} without a
SUBMIT attempt, draft SOMETHING and submit — an imperfect SUBMIT that gets
validator feedback is strictly better than a final turn with no submission.

Every tool result shows "turns_remaining". When it drops to {URGENCY_TURNS},
SUBMIT immediately regardless of how polished your task feels.

CITY: {city.title()}
WINDOW: {window.get('label', '?')} ({date_range})
ANCHOR EVENTS: {anchor_str}
POOL: {len(pool)} venues ({n_with_coords} with coordinates, {n_sold_out_venues} with sold-out dates)
Pool breakdown: {json.dumps(cat_counts)}

WINDOW CHARACTER:
{window.get('character', '')}{(chr(10) + chr(10) + 'WINDOW WEATHER: ' + window['weather_notes']) if window.get('weather_notes') else ''}

TRAVEL FORMULA (lng_factor={lng_factor} for this city):
dist_km ≈ sqrt((Δlat×111)² + (Δlng×{lng_factor})²)  walk=dist×12min  transit=dist×8+5min
Or use estimate_travel(venue_id_a, venue_id_b, mode) for specific pairs.

VENUE POOL (condensed — use get_venue for full detail):
Format: venue_id | name | category | district | tier | pace | cost | lat,lng | [flags]
[SOLD]=sold-out dates in window  [stale:category]=venue has outdated/incorrect data (type: temporal_decay|propagation_error|conditional|subjective)  [activates:date-range]=conditional stale data only active during these dates
{chr(10).join(pool_lines)}

{protocol}

TASK SCHEMA — your SUBMIT must produce JSON with these top-level fields:
  city, window_id, days, start_date, public_input (with query),
  (task_id is auto-generated from model/city/type/date on SUBMIT — any placeholder is fine)
  structural_type, difficulty, rubric (with hard_constraints, personal_constraints,
  b_score_constraints: [], required_venue_ids)

Standard hard constraints (always include all 3):
  {{"id":"hc_001","type":"hours_check","check_method":"code","params":{{}}}}
  {{"id":"hc_002","type":"no_overlap","check_method":"code","params":{{}}}}
  {{"id":"hc_003","type":"travel_time_hard","check_method":"code","params":{{}}}}

═══════════════════════════════════════════════════════════════════════════
PERSONAL CONSTRAINT SCHEMA — getting this wrong is the #1 SUBMIT failure.
Each entry in personal_constraints must have ALL eight fields:
═══════════════════════════════════════════════════════════════════════════
  {{
    "id":                "pc_001",
    "score_tier":        "P",                   // literal "P" — not "hard"
    "hop":               1 or 2,                // integer
    "check_method":      "code",
    "source_in_profile": "<verbatim phrase from query>",
    "description":       "<human-readable>",
    "scope":             "<see below>",
    "condition":         {{...}},
    "aggregation":       "<see below>",
    "consequence":       "p_score_full"
  }}

DO NOT use "pattern"/"params" — that's the old format and will be rejected.

SCOPE (which activities this constraint applies to):
  "all"                         all activities in the plan
  "activity_type=meal"          meal activities only (restaurants, cafes, bars)
  "activity_type=visit"         visit activities only (museums, attractions, parks)
  "per_day"                     applied once per day (use with at_most for daily caps)
  "category=museum"             activities at museum-category venues only
  "has_tag=halal"               activities at venues carrying this tag
  "venue_id=lon_abc123"         the specific venue with that id
  ["activity_type=meal", "time_window=18:00-23:59"]   AND: evening meals only

CONDITION (what each in-scope activity must satisfy):
  {{"has_tag": "vegetarian"}}
  {{"not_tag": "tourist-trap"}}
  {{"field": "noise_level", "operator": "<=", "value": "moderate"}}
  {{"field": "price_tier", "operator": ">=", "value": "upscale"}}
  {{"field": "wheelchair_accessible", "operator": "==", "value": true}}
  {{}}                                        no condition (scope-filter only)
  {{"all": [cond1, cond2]}}                   AND of conditions
  {{"any": [cond1, cond2]}}                   OR of conditions
     Examples:
       "all": [{{"has_tag":"free-entry"}},{{"has_tag":"art"}}]  → free AND art
       "any": [{{"field":"district","operator":"==","value":"Hackney"}},
                {{"field":"district","operator":"==","value":"Tower Hamlets"}}]
               → Hackney OR Tower Hamlets (use "any" for multi-district/value OR)

AGGREGATION (how in-scope activities collectively satisfy):
  "all"                          every in-scope activity must satisfy
  "none"                         no in-scope activity may satisfy
  {{"at_least": 2}}              at least 2 in-scope activities satisfy
  {{"at_most": 1}}               at most 1 in-scope activity satisfies
  {{"at_most_distinct": 2, "field": "district"}}  at most 2 distinct districts
  {{"count_distinct": 3, "field": "district"}}       at least 3 distinct values
  {{"count_distinct": 3, "field": "cuisine"}}        at least 3 distinct cuisines
                                                  (restaurants only; reads from
                                                   venues.cuisine column)
  {{"ratio": 0.6}}               ≥60% of in-scope activities satisfy condition
  {{"at_least_days": 1}}         at least 1 complete day where all satisfy
  (Full aggregation reference: '-h validate')

═══════════════════════════════════════════════════════════════════════════
HOW CONSTRAINTS APPLY — COMMON SOURCE OF SOLVABILITY FAILURE
═══════════════════════════════════════════════════════════════════════════
Universal constraints (aggregation: "all") ARE pool filters — they remove
venues that don't satisfy. Inclusion constraints (aggregation: {{"at_least":N}})
are NOT pool filters — they check the plan, not the pool. This distinction matters.

  ✗ aggregation:"all" + scope:"has_tag=outdoor-seating" + scope:"category=museum"
    → pool filtered to only museums with outdoor-seating. 0 survive in most pools.

  ✓ aggregation:{{"at_least":2}} + scope:"category=museum" + separate
    universal constraint for the outdoor signal
    → pool keeps all venues; plan must include ≥2 museums.

Before combining universal constraints, run query_pool for the intersection
and count survivors. If intersection < your min, the task is unsolvable.

Concrete verification workflow before every SUBMIT:
1. query_pool with each universal constraint's scope as a filter.
2. Check survivor count after each filter compounds.
3. If survivors drop below what inclusion constraints need, loosen or remove
   one universal filter, or state one side via query wording instead.
═══════════════════════════════════════════════════════════════════════════
Every task needs ≥1 hop-2 P-constraint. source_in_profile must be a verbatim
phrase from the query (not "inferred" or "N/A"). "type" in a P-constraint
is always wrong — hard_constraints use "type", personal_constraints use scope/condition/aggregation.

═══════════════════════════════════════════════════════════════════════════
ADDITIONAL GENERIC SCHEMA EXAMPLES — cuisine and cross-day variety
═══════════════════════════════════════════════════════════════════════════
These common patterns use the standard scope/condition/aggregation format:

// "At least 60% of meals must be local cuisine" (ratio aggregation)
{{
  "id": "pc_001", "score_tier": "P", "hop": 1, "check_method": "code",
  "source_in_profile": "want to eat like a local",
  "description": "At least 60% of meals at local cuisine venues",
  "scope": "activity_type=meal",
  "condition": {{"field": "local_cuisine", "operator": "==", "value": 1}},
  "aggregation": {{"ratio": 0.6}},
  "consequence": "p_score_full"
}},
// "Try at least 3 distinct cuisines across meals" (count_distinct aggregation)
{{
  "id": "pc_002", "score_tier": "P", "hop": 1, "check_method": "code",
  "source_in_profile": "try as many different cuisines as possible",
  "description": "At least 3 distinct cuisine types across meals",
  "scope": "activity_type=meal",
  "condition": {{}},
  "aggregation": {{"count_distinct": 3, "field": "cuisine"}},
  "consequence": "p_score_full"
}},
// "At most 1 museum per day" (per_day scope + at_most aggregation)
{{
  "id": "pc_003", "score_tier": "P", "hop": 1, "check_method": "code",
  "source_in_profile": "don't want to spend all day in museums",
  "description": "No more than one museum per day",
  "scope": "per_day",
  "condition": {{"field": "category", "operator": "==", "value": "museum"}},
  "aggregation": {{"at_most": 1}},
  "consequence": "p_score_full"
}},
// "Mostly outdoor activities" — indoor/outdoor balance via ratio on tag
// Use min_ratio for "mostly outdoor", max_ratio for "mostly indoor",
// both for a balanced range like 40-60% outdoor.
{{
  "id": "pc_004", "score_tier": "P", "hop": 2, "check_method": "code",
  "source_in_profile": "we love being outside",
  "description": "At least 50% of activities at outdoor venues",
  "scope": "all",
  "condition": {{"has_tag": "outdoor"}},
  "aggregation": {{"ratio": 0.5}},
  "consequence": "p_score_full"
}},
// "Keep travel under 60 minutes per day" — daily travel time budget
// travel_minutes is set on each activity from the travel matrix at eval time.
// Use per_day scope + sum aggregation.
{{
  "id": "pc_005", "score_tier": "P", "hop": 2, "check_method": "code",
  "source_in_profile": "don't want to spend the whole day commuting",
  "description": "Total travel time per day under 60 minutes",
  "scope": "per_day",
  "condition": {{}},
  "aggregation": {{"sum": "travel_minutes", "operator": "<=", "value": 60}},
  "consequence": "p_score_full"
}}

═══════════════════════════════════════════════════════════════════════════
SPECIAL-HANDLER P-CONSTRAINTS — use pattern+params, NOT scope/condition
═══════════════════════════════════════════════════════════════════════════
A small set of P-constraints require special logic that the generic engine
cannot express — they need external state or cross-activity relational
logic. These use the OLD pattern+params format. Only 4 patterns remain:

  {{
    "id":                "pc_001",
    "score_tier":        "P",
    "hop":               1,
    "pattern":           "<see patterns below>",
    "check_method":      "code",
    "source_in_profile": "<verbatim phrase>",
    "description":       "<human-readable>",
    "params":            {{...}}
  }}

SPECIAL PATTERNS:

  weather_aware
    — Venues sensitive to weather (outdoor venues, rooftop bars, etc.) must
      not be scheduled when forecast is worse than max_condition. Reads the
      city weather forecast for the task dates.
    params: {{"max_condition": "cloudy", "activity_type": "any"}}
    // max_condition: "sunny"|"partly_cloudy"|"cloudy"|"rainy"|"stormy"

  opening_time_required
    — Venue must open before a given time. Useful for early-bird itineraries
      ("I like to start early") or late-night restrictions.
    params: {{"open_before": "10:00", "activity_type": "visit"}}

  dependency_chain
    — An activity at an anchor venue requires a supporting activity nearby
      before or after it within a walk window.
    params: {{"anchor_venue_id": "<id>", "requirement": "meal_within_walk",
             "max_walk_minutes": 15, "timing": "before"}}

  consecutive_pairs
    — Adjacent activity pairs in the plan must satisfy a relational predicate.
      Used for structural B-score checks (alternating venue types, etc.).
    params: {{"check_type": "alternates", "field": "recommended_pace",
             "threshold": 0.5}}

NOTE: Use these only when the query clearly calls for this exact semantic.
For most tasks, the generic scope/condition/aggregation format is preferred.

═══════════════════════════════════════════════════════════════════════════
CHARACTER TRAIT RULES — hard limit on personal constraint variety
═══════════════════════════════════════════════════════════════════════════
At most ONE Cat 2 (physical/dietary) signal per task. Cat 2 includes any
constraint about dietary requirements, mobility, or personal restrictions:
  vegetarian, vegan, halal, kosher, gluten-free,
  wheelchair_accessible, pet_friendly, family_friendly,
  allergy-related, age_restriction.

  ✗ condition:{{has_tag:vegetarian-options}} + condition:{{wheelchair_accessible==true}}
    → TWO Cat 2 signals (dietary + mobility) — fails validation.
  ✓ condition:{{has_tag:vegetarian-options}} alone, or condition:{{wheelchair_accessible==true}} alone.

Exception: Type 5 (hard feasibility reduction) MAY stack multiple Cat 2
signals — narrowing the pool IS the point of that type.

Use HELP '-h validate' for the full schema reference with worked examples.
Use HELP '-h tools' for the tool reference with a SUBMIT call example.
Use THINK liberally — reasoning before tool calls produces better tasks.

REMEMBER: every turn must be a tool call. Start with THINK.
"""


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-MODEL AGENT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def _truncate_for_log(obj, max_len: int = 2000):
    """
    Render an object safely for JSON logging, truncating long strings in place.
    Keeps structure but caps individual fields at max_len chars — so a 50kB
    task_json body in a SUBMIT call doesn't bloat the transcript.
    """
    if isinstance(obj, str):
        return obj if len(obj) <= max_len else obj[:max_len] + f"... [truncated, total {len(obj)} chars]"
    if isinstance(obj, dict):
        return {k: _truncate_for_log(v, max_len) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_for_log(v, max_len) for v in obj]
    return obj


def _run_openai_loop(
    messages: list[dict],
    system_prompt: str,
    model: str,
    api_key: str,
    base_url: str | None,
    pool: list[dict],
    pool_map: dict,
    window: dict,
    city: str,
    handbook: dict,
    unavailable: dict,
    verbose: bool,
    type_key: str = "",
    max_turns: int = MAX_TURNS,
    _used_venue_ids: set = None,
    _used_tension_axes: set = None,
) -> dict | None:
    """Run agent loop using OpenAI-compatible API (GPT, DeepSeek)."""
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    is_reasoner = "reasoner" in model.lower()
    max_tokens  = 16000 if is_reasoner else 6000

    # OpenAI deprecated `max_tokens` for GPT-5 family; it now requires
    # `max_completion_tokens`. Older models (gpt-4*, gpt-3.5*) still use
    # `max_tokens`. Route by model family.
    _ml = model.lower()
    _uses_new_param = (
        _ml.startswith("gpt-5")     # gpt-5, gpt-5.4, gpt-5.4-mini, etc.
        or _ml.startswith("o1")      # o1, o1-mini, o1-preview
        or _ml.startswith("o3")      # o3, o3-mini
        or _ml.startswith("o4")      # o4-mini, etc.
    )
    token_kwarg = "max_completion_tokens" if _uses_new_param else "max_tokens"

    accepted_task = None
    turn = 0
    stop_reason = "exhausted"
    last_submit_errors: list[str] = []
    consecutive_no_tool = 0
    NO_TOOL_LIMIT = 2   # after this many prose-only responses in a row, give up
    transcript: list[dict] = []   # normalized per-turn log for debugging
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _mk_stop_info(reason: str) -> dict:
        return {
            "turns_used": turn,
            "max_turns":  max_turns,
            "stop_reason": reason,
            "last_submit_errors": last_submit_errors,
            "transcript": transcript,
            "usage_totals": totals,
        }

    while turn < max_turns:
        turn += 1
        all_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            _call_kwargs = {
                "model":    model,
                "tools":    TASK_TOOL_SCHEMAS_OPENAI,
                "messages": all_messages,
                "stream":   False,
                token_kwarg: max_tokens,
            }
            resp = client.chat.completions.create(**_call_kwargs)
            usage = _extract_usage(resp, "openai")
            for k, v in usage.items():
                totals[k] = totals.get(k, 0) + v
        except Exception as _api_exc:
            import traceback as _tb
            transcript.append({
                "turn":            turn,
                "assistant_text": "",
                "tool_calls":      [],
                "api_error":       f"{type(_api_exc).__name__}: {_api_exc}",
                "traceback_excerpt": _tb.format_exc().splitlines()[-3:],
            })
            stop_reason = f"api_error:{type(_api_exc).__name__}"
            break

        msg    = resp.choices[0].message
        finish = resp.choices[0].finish_reason

        # Append assistant message
        messages.append(msg.model_dump(exclude_unset=False))

        # Build normalized transcript entry for this turn
        turn_entry: dict = {
            "turn": turn,
            "assistant_text": msg.content or "",
            "finish_reason":  finish,
            "tool_calls":     [],
            "nudge_sent":     False,
            "usage":          usage,
        }

        if verbose:
            if msg.content:
                print(f"    [turn {turn}] assistant: {msg.content[:120]}...", flush=True)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    print(f"    [turn {turn}] tool call: {tc.function.name}", flush=True)
            print(f"    [turn {turn}] tokens in={usage['input_tokens']} out={usage['output_tokens']}", flush=True)

        # ── Handle prose-only responses (agent failed to call a tool) ──
        # Some models (DeepSeek-chat in particular) like to write the task JSON
        # directly in the assistant message body without invoking the SUBMIT tool.
        # Don't silently break — nudge them to use the tool. Only give up after
        # NO_TOOL_LIMIT consecutive prose-only turns, so one stray response
        # doesn't kill an otherwise-progressing run.
        if not msg.tool_calls:
            consecutive_no_tool += 1
            turn_entry["nudge_sent"] = True
            transcript.append(turn_entry)
            if consecutive_no_tool >= NO_TOOL_LIMIT:
                stop_reason = "no_tool_calls"
                break

            nudge = (
                "Your previous message contained no tool call. You cannot complete this "
                "task by writing JSON in a message body — the validation system only runs "
                "when you invoke the SUBMIT tool. "
                "Please call the SUBMIT tool now with task_json as an argument, e.g.:\n"
                '  SUBMIT(task_json="{...your full task JSON...}")\n'
                "If you need more tool calls first (THINK, query_pool, etc.), do those "
                "as actual tool invocations — do not describe them in prose."
            )
            messages.append({"role": "user", "content": nudge})
            if verbose:
                print(f"    [turn {turn}] ⚠ no tool call — nudging agent to use SUBMIT", flush=True)
            continue

        # Reset nudge counter on any real tool call
        consecutive_no_tool = 0

        # Process tool calls
        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                tool_input = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_input = {}

            try:
                result, terminate = _dispatch_task_tool(
                    tool_name, tool_input, pool, pool_map, window, city, handbook, unavailable,
                    type_key=type_key, _used_venue_ids=_used_venue_ids or set(),
                    _used_tension_axes=_used_tension_axes or set(),
                    model=model,
                )
            except Exception as _dispatch_exc:
                # Tool dispatch crashed — most commonly an LLM sending a
                # malformed argument that hits an unguarded code path in
                # pool_utils / validator. Record the crash in the transcript
                # as a failed tool result and let the agent see it on the
                # next turn instead of aborting the whole run.
                import traceback as _tb
                result = {
                    "status": "tool_error",
                    "error":  f"{type(_dispatch_exc).__name__}: {_dispatch_exc}",
                    "traceback_excerpt": _tb.format_exc().splitlines()[-3:],
                    "hint":   (
                        "Tool dispatch raised an exception. "
                        + (
                            "A nested task field is a JSON-encoded string instead of a dict "
                            "(e.g. public_input or rubric passed as a string). "
                            "Check that public_input, rubric, and all nested objects are "
                            "real dicts, not strings. Fix the task JSON and SUBMIT again."
                            if "has no attribute 'get'" in str(_dispatch_exc)
                            else
                            "Check your argument types match the handbook schema exactly."
                        )
                    ),
                }
                terminate = False

            if not terminate:
                turns_remaining = max_turns - turn
                result["turns_remaining"] = turns_remaining
                if turns_remaining <= URGENCY_TURNS:
                    result["WARNING"] = (
                        f"Only {turns_remaining} turn(s) left! "
                        f"Call SUBMIT NOW with your best draft — an imperfect submission beats no submission."
                    )

            # Track the most recent SUBMIT failure for diagnostic output
            if tool_name == "SUBMIT" and result.get("status") == "validation_failed":
                last_submit_errors = result.get("errors", [])

            # Record in transcript (truncate large blobs for readability)
            turn_entry["tool_calls"].append({
                "name":   tool_name,
                "input":  _truncate_for_log(tool_input),
                "status": result.get("status", "?"),
                "result": _truncate_for_log(result),
            })

            if verbose:
                print(f"    [turn {turn}] {tool_name} → {result.get('status','?')}"
                      + (f" ({result.get('count','')} venues)" if tool_name == "query_pool" else "")
                      + (f" errors={result.get('errors',[])}" if result.get('status') == 'validation_failed' else ""),
                      flush=True)

            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      json.dumps(result, ensure_ascii=False),
            })

            if terminate:
                # Use the corrected task from _dispatch_task_tool (window_id, difficulty normalised)
                accepted_task = result.get("_accepted_task")
                if accepted_task is None:
                    try:
                        task_json_str = tool_input.get("task_json", "{}")
                        accepted_task = json.loads(task_json_str) if isinstance(task_json_str, str) else task_json_str
                    except Exception:
                        pass
                stop_reason = "submitted"
                transcript.append(turn_entry)
                return accepted_task, _mk_stop_info(stop_reason)

        transcript.append(turn_entry)

    return None, _mk_stop_info(stop_reason)


def _run_anthropic_loop(
    initial_message: str,
    system_prompt: str,
    model: str,
    api_key: str,
    pool: list[dict],
    pool_map: dict,
    window: dict,
    city: str,
    handbook: dict,
    unavailable: dict,
    verbose: bool,
    type_key: str = "",
    max_turns: int = MAX_TURNS,
    _used_venue_ids: set = None,
    _used_tension_axes: set = None,
) -> dict | None:
    """Run agent loop using Anthropic API."""
    client    = _anthropic_mod.Anthropic(api_key=api_key)
    tools     = _openai_to_anthropic_tools(TASK_TOOL_SCHEMAS_OPENAI)
    messages  = [{"role": "user", "content": initial_message}]

    # Prompt caching: mark the system prompt and the tool list as cacheable.
    # Both are byte-identical across all turns of a single task, so every turn
    # after the first reads them at ~10% input cost. 5-min TTL is fine — a
    # 20-turn loop typically completes inside that window.
    system_blocks = [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]
    if tools:
        # Marking the LAST tool caches the entire tool array up to that point.
        tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]

    accepted_task = None
    turn = 0
    stop_reason = "exhausted"
    last_submit_errors: list[str] = []
    consecutive_no_tool = 0
    NO_TOOL_LIMIT = 2
    transcript: list[dict] = []
    totals = {"input_tokens": 0, "output_tokens": 0,
              "cache_creation_input_tokens": 0,
              "cache_read_input_tokens": 0,
              "total_tokens": 0}

    def _mk_stop_info(reason: str) -> dict:
        return {
            "turns_used": turn,
            "max_turns":  max_turns,
            "stop_reason": reason,
            "last_submit_errors": last_submit_errors,
            "transcript": transcript,
            "usage_totals": totals,
        }

    while turn < max_turns:
        turn += 1

        try:
            resp = client.messages.create(
                model=model,
                max_tokens=8000,
                system=system_blocks,
                tools=tools,
                messages=messages,
            )
            usage = _extract_usage(resp, "anthropic")
            for k, v in usage.items():
                totals[k] = totals.get(k, 0) + v
        except Exception as _api_exc:
            import traceback as _tb
            transcript.append({
                "turn":            turn,
                "assistant_text": "",
                "tool_calls":      [],
                "api_error":       f"{type(_api_exc).__name__}: {_api_exc}",
                "traceback_excerpt": _tb.format_exc().splitlines()[-3:],
            })
            stop_reason = f"api_error:{type(_api_exc).__name__}"
            break

        # Build assistant message content
        content_blocks = resp.content
        messages.append({"role": "assistant", "content": content_blocks})

        # Collect assistant text for transcript
        assistant_text = "".join(
            getattr(b, "text", "") for b in content_blocks if hasattr(b, "text")
        )
        turn_entry: dict = {
            "turn": turn,
            "assistant_text": assistant_text,
            "stop_reason_api": resp.stop_reason,
            "tool_calls":     [],
            "nudge_sent":     False,
            "usage":          usage,
        }

        if verbose:
            for block in content_blocks:
                if hasattr(block, "text") and block.text:
                    print(f"    [turn {turn}] assistant text: {block.text[:120]}...", flush=True)
                elif hasattr(block, "name"):
                    print(f"    [turn {turn}] tool call: {block.name}", flush=True)
            print(f"    [turn {turn}] tokens in={usage['input_tokens']} out={usage['output_tokens']}"
                  + (f" cache_read={usage.get('cache_read_input_tokens', 0)}"
                     if usage.get('cache_read_input_tokens') else ""), flush=True)

        # ── Handle no-tool-use stop ──
        # Anthropic signals prose-only completion with stop_reason="end_turn".
        # Nudge the agent to use SUBMIT rather than silently bailing.
        has_tool_use = any(getattr(b, "type", "") == "tool_use" for b in content_blocks)
        if resp.stop_reason == "end_turn" or not has_tool_use:
            consecutive_no_tool += 1
            turn_entry["nudge_sent"] = True
            transcript.append(turn_entry)
            if consecutive_no_tool >= NO_TOOL_LIMIT:
                stop_reason = "no_tool_calls"
                break

            nudge = (
                "Your previous message contained no tool call. You cannot complete this "
                "task by writing JSON in a message body — the validation system only runs "
                "when you invoke the SUBMIT tool. "
                "Please call the SUBMIT tool now with task_json as an argument. "
                "If you need more tool calls first (THINK, query_pool, etc.), do those "
                "as actual tool invocations — do not describe them in prose."
            )
            messages.append({"role": "user", "content": nudge})
            if verbose:
                print(f"    [turn {turn}] ⚠ no tool call — nudging agent to use SUBMIT", flush=True)
            continue

        if resp.stop_reason != "tool_use":
            # Some other stop reason (e.g. max_tokens). Treat as failure.
            stop_reason = f"stop_reason:{resp.stop_reason}"
            transcript.append(turn_entry)
            break

        consecutive_no_tool = 0

        # Process tool use blocks
        tool_results = []
        terminate_after = False

        for block in content_blocks:
            if block.type != "tool_use":
                continue

            tool_name  = block.name
            tool_input = block.input if isinstance(block.input, dict) else {}

            try:
                result, terminate = _dispatch_task_tool(
                    tool_name, tool_input, pool, pool_map, window, city, handbook, unavailable,
                    type_key=type_key, _used_venue_ids=_used_venue_ids or set(),
                    _used_tension_axes=_used_tension_axes or set(),
                    model=model,
                )
            except Exception as _dispatch_exc:
                import traceback as _tb
                result = {
                    "status": "tool_error",
                    "error":  f"{type(_dispatch_exc).__name__}: {_dispatch_exc}",
                    "traceback_excerpt": _tb.format_exc().splitlines()[-3:],
                    "hint":   (
                        "Tool dispatch raised an exception. "
                        + (
                            "A nested task field is a JSON-encoded string instead of a dict "
                            "(e.g. public_input or rubric passed as a string). "
                            "Check that public_input, rubric, and all nested objects are "
                            "real dicts, not strings. Fix the task JSON and SUBMIT again."
                            if "has no attribute 'get'" in str(_dispatch_exc)
                            else
                            "Check your argument types match the handbook schema exactly."
                        )
                    ),
                }
                terminate = False

            if not terminate:
                turns_remaining = max_turns - turn
                result["turns_remaining"] = turns_remaining
                if turns_remaining <= URGENCY_TURNS:
                    result["WARNING"] = (
                        f"Only {turns_remaining} turn(s) left! "
                        f"Call SUBMIT NOW with your best draft — an imperfect submission beats no submission."
                    )

            if tool_name == "SUBMIT" and result.get("status") == "validation_failed":
                last_submit_errors = result.get("errors", [])

            # Record in transcript
            turn_entry["tool_calls"].append({
                "name":   tool_name,
                "input":  _truncate_for_log(tool_input),
                "status": result.get("status", "?"),
                "result": _truncate_for_log(result),
            })

            if verbose:
                print(f"    [turn {turn}] {tool_name} → {result.get('status','?')}", flush=True)

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     json.dumps(result, ensure_ascii=False),
            })

            if terminate:
                accepted_task = result.get("_accepted_task")
                if accepted_task is None:
                    try:
                        task_json_str = tool_input.get("task_json", "{}")
                        accepted_task = json.loads(task_json_str) if isinstance(task_json_str, str) else task_json_str
                    except Exception:
                        pass
                terminate_after = True

        messages.append({"role": "user", "content": tool_results})
        transcript.append(turn_entry)

        if terminate_after:
            stop_reason = "submitted"
            return accepted_task, _mk_stop_info(stop_reason)

    return None, _mk_stop_info(stop_reason)


def _run_gemini_loop(
    initial_message: str,
    system_prompt: str,
    model: str,
    api_key: str,
    pool: list[dict],
    pool_map: dict,
    window: dict,
    city: str,
    handbook: dict,
    unavailable: dict,
    verbose: bool,
    type_key: str = "",
    max_turns: int = MAX_TURNS,
    _used_venue_ids: set = None,
    _used_tension_axes: set = None,
) -> dict | None:
    """Run agent loop using Gemini API."""
    client = _google_genai.Client(api_key=api_key)

    # Build Gemini tool declarations from OpenAI schemas
    from google.genai import types as gt
    tool_decls = []
    for s in TASK_TOOL_SCHEMAS_OPENAI:
        fn = s["function"]
        params = fn.get("parameters", {})
        tool_decls.append(gt.Tool(function_declarations=[
            gt.FunctionDeclaration(
                name=fn["name"],
                description=fn.get("description", ""),
                parameters=params,
            )
        ]))

    contents = [
        gt.Content(role="user", parts=[gt.Part(text=initial_message)])
    ]
    config = gt.GenerateContentConfig(
        system_instruction=system_prompt,
        max_output_tokens=8000,
        tools=tool_decls,
    )

    accepted_task = None
    turn = 0
    stop_reason = "exhausted"
    last_submit_errors: list[str] = []
    consecutive_no_tool = 0
    NO_TOOL_LIMIT = 2
    transcript: list[dict] = []
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _mk_stop_info(reason: str) -> dict:
        return {
            "turns_used": turn,
            "max_turns":  max_turns,
            "stop_reason": reason,
            "last_submit_errors": last_submit_errors,
            "transcript": transcript,
            "usage_totals": totals,
        }

    while turn < max_turns:
        turn += 1
        try:
            resp = client.models.generate_content(
                model=model, contents=contents, config=config
            )
            usage = _extract_usage(resp, "gemini")
            for k, v in usage.items():
                totals[k] = totals.get(k, 0) + v
        except Exception as _api_exc:
            import traceback as _tb
            transcript.append({
                "turn":            turn,
                "assistant_text": "",
                "tool_calls":      [],
                "api_error":       f"{type(_api_exc).__name__}: {_api_exc}",
                "traceback_excerpt": _tb.format_exc().splitlines()[-3:],
            })
            stop_reason = f"api_error:{type(_api_exc).__name__}"
            break

        # Guard: Gemini can return content=None or parts=None when a response
        # is blocked (safety filter, RECITATION, or MAX_TOKENS on first call).
        # Without this guard the "".join crashes and kills the whole run.
        _candidate = resp.candidates[0] if resp.candidates else None
        _content   = getattr(_candidate, "content", None)
        _parts     = getattr(_content, "parts", None) or []

        if not _candidate or _content is None:
            _finish = getattr(_candidate, "finish_reason", "unknown") if _candidate else "no_candidates"
            transcript.append({
                "turn": turn,
                "assistant_text": "",
                "tool_calls": [],
                "api_error": (
                    f"Gemini returned no usable content (finish_reason={_finish}). "
                    "Possible safety filter, RECITATION block, or prompt too large. "
                    "Aborting run."
                ),
            })
            stop_reason = f"api_error:no_content:{_finish}"
            break

        # Add model response to contents
        contents.append(_content)

        # Collect text + prepare transcript entry
        model_text = "".join(
            getattr(p, "text", "") or "" for p in _parts
            if hasattr(p, "text")
        )
        turn_entry: dict = {
            "turn": turn,
            "assistant_text": model_text,
            "tool_calls":     [],
            "nudge_sent":     False,
            "usage":          usage,
        }

        if verbose:
            for part in _parts:
                if hasattr(part, "text") and part.text:
                    print(f"    [turn {turn}] model: {part.text[:120]}...", flush=True)
                elif hasattr(part, "function_call") and part.function_call:
                    print(f"    [turn {turn}] tool: {part.function_call.name}", flush=True)
            print(f"    [turn {turn}] tokens in={usage['input_tokens']} out={usage['output_tokens']}", flush=True)

        # Check for function calls
        fn_calls = [p for p in _parts
                    if hasattr(p, "function_call") and p.function_call]

        if not fn_calls:
            # Prose-only response — nudge the agent to use SUBMIT
            consecutive_no_tool += 1
            turn_entry["nudge_sent"] = True
            transcript.append(turn_entry)
            if consecutive_no_tool >= NO_TOOL_LIMIT:
                stop_reason = "no_tool_calls"
                break
            nudge_text = (
                "Your previous message contained no tool call. You cannot complete this "
                "task by writing JSON in a message body — the validation system only runs "
                "when you invoke the SUBMIT tool. "
                "Please call the SUBMIT tool now with task_json as an argument. "
                "If you need more tool calls first (THINK, query_pool, etc.), do those "
                "as actual tool invocations — do not describe them in prose."
            )
            contents.append(gt.Content(role="user", parts=[gt.Part(text=nudge_text)]))
            if verbose:
                print(f"    [turn {turn}] ⚠ no tool call — nudging agent to use SUBMIT", flush=True)
            continue

        consecutive_no_tool = 0

        # Process tool calls
        fn_responses = []
        terminate_after = False

        for part in fn_calls:
            fc = part.function_call
            tool_name  = fc.name
            tool_input = dict(fc.args) if fc.args else {}

            try:
                result, terminate = _dispatch_task_tool(
                    tool_name, tool_input, pool, pool_map, window, city, handbook, unavailable,
                    type_key=type_key, _used_venue_ids=_used_venue_ids or set(),
                    _used_tension_axes=_used_tension_axes or set(),
                    model=model,
                )
            except Exception as _dispatch_exc:
                import traceback as _tb
                result = {
                    "status": "tool_error",
                    "error":  f"{type(_dispatch_exc).__name__}: {_dispatch_exc}",
                    "traceback_excerpt": _tb.format_exc().splitlines()[-3:],
                    "hint":   (
                        "Tool dispatch raised an exception. "
                        + (
                            "A nested task field is a JSON-encoded string instead of a dict "
                            "(e.g. public_input or rubric passed as a string). "
                            "Check that public_input, rubric, and all nested objects are "
                            "real dicts, not strings. Fix the task JSON and SUBMIT again."
                            if "has no attribute 'get'" in str(_dispatch_exc)
                            else
                            "Check your argument types match the handbook schema exactly."
                        )
                    ),
                }
                terminate = False

            if not terminate:
                turns_remaining = max_turns - turn
                result["turns_remaining"] = turns_remaining
                if turns_remaining <= URGENCY_TURNS:
                    result["WARNING"] = (
                        f"Only {turns_remaining} turn(s) left! "
                        f"Call SUBMIT NOW with your best draft — an imperfect submission beats no submission."
                    )

            if tool_name == "SUBMIT" and result.get("status") == "validation_failed":
                last_submit_errors = result.get("errors", [])

            # Record in transcript
            turn_entry["tool_calls"].append({
                "name":   tool_name,
                "input":  _truncate_for_log(tool_input),
                "status": result.get("status", "?"),
                "result": _truncate_for_log(result),
            })

            if verbose:
                print(f"    [turn {turn}] {tool_name} → {result.get('status','?')}", flush=True)

            fn_responses.append(gt.Part(
                function_response=gt.FunctionResponse(
                    name=tool_name,
                    response={"result": json.dumps(result, ensure_ascii=False)},
                )
            ))

            if terminate:
                accepted_task = result.get("_accepted_task")
                if accepted_task is None:
                    try:
                        task_json_str = tool_input.get("task_json", "{}")
                        accepted_task = json.loads(task_json_str) if isinstance(task_json_str, str) else task_json_str
                    except Exception:
                        pass
                terminate_after = True

        contents.append(gt.Content(role="user", parts=fn_responses))
        transcript.append(turn_entry)

        if terminate_after:
            stop_reason = "submitted"
            return accepted_task, _mk_stop_info(stop_reason)

    return None, _mk_stop_info(stop_reason)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def run_task_agent(
    city: str,
    window: dict,
    pool: list[dict],
    type_key: str,
    model: str,
    api_key: str,
    db_path: Path = None,
    max_turns: int = MAX_TURNS,
    unavailable: dict | None = None,
    centre_lat: float | None = None,
    centre_lng: float | None = None,
    verbose: bool = False,
    log_dir: Path | None = None,
    used_venue_ids: set | None = None,
    used_tension_axes: set | None = None,
) -> tuple[dict | None, dict]:
    """
    Run the task generation agent loop.

    Returns (accepted_task | None, stop_info) where stop_info is:
        {
          "turns_used": int,          # how many turns the loop actually ran
          "max_turns":  int,          # the cap that was in effect
          "stop_reason": str,         # one of:
                                      #   "submitted"      — clean SUBMIT, task accepted
                                      #   "exhausted"      — ran out of turns
                                      #   "no_tool_calls"  — agent wrote prose with no
                                      #                      tool_call for NO_TOOL_LIMIT
                                      #                      consecutive turns (typical
                                      #                      DeepSeek-chat failure mode)
                                      #   "stop_reason:X"  — Anthropic returned an
                                      #                      unexpected stop_reason
          "last_submit_errors": list  # validation errors from the most recent SUBMIT
                                      #   (useful when agent tried but couldn't pass)
        }

    The loop ONLY accepts a task via a successful SUBMIT tool call. If a model
    writes the task JSON as prose without invoking SUBMIT, the loop appends a
    nudge message asking it to use the tool and continues. After NO_TOOL_LIMIT
    (=2) consecutive prose-only turns, the loop gives up.

    Args:
        city:        city name
        window:      window dict with dates, anchor_events, character
        pool:        list of venue dicts (from load_venue_pool / load_city_pool)
        type_key:    structural type e.g. "type2" or "type2_subset_selection"
        model:       model string
        api_key:     API key for the model
        db_path:     SQLite DB path
        unavailable: {venue_id: [sold_out_date, ...]} from load_unavailable_dates
        centre_lat:  city centre latitude (derived from pool if None)
        verbose:     print turn-by-turn tool calls
    """
    if db_path is None:
        db_path = get_city_db_path(city)
    unavailable = unavailable or {}
    pool_map    = {v["venue_id"]: v for v in pool}
    _used_venue_ids = used_venue_ids if used_venue_ids is not None else set()
    _used_tension_axes = used_tension_axes if used_tension_axes is not None else set()

    # No API key → cannot run agent loop
    if not api_key:
        return None, {"stop_reason": "no_api_key", "turns_used": 0,
                      "max_turns": max_turns, "last_submit_errors": []}

    # Derive city centre if not provided
    if centre_lat is None:
        centre_lat, centre_lng = _city_centre_from_pool(pool)
    elif centre_lng is None:
        centre_lng = _city_centre_from_pool(pool)[1]

    # Build dynamic handbook
    handbook = _build_task_handbook(pool, window, city)

    # Build system prompt
    system_prompt = _build_agent_system_prompt(
        type_key, city, window, pool,
        centre_lat, centre_lng, unavailable
    )

    # Initial user message — kick off the loop
    initial_message = (
        f"""
        Generate a {type_key} benchmark task for {city} in the '{window.get('label', '?')}' window.

        BEFORE YOU START — read the handbook. It contains pool-specific data that
        varies per city and is NOT in your system prompt:

        HELP('-h tags')         — REQUIRED. All tags in this pool with counts.
                                    You cannot use has_tag conditions without this.
        HELP('-h regulations')  — REQUIRED. Regulation fields and their true/false
                                    distributions. Constraints on regulations with
                                    100% one-sided values are unsolvable.
        HELP('-h pool_fields')  — REQUIRED. All filterable fields for query_pool.
                                    Do not guess field names.
        HELP('-h window')       — REQUIRED for type6. Anchor event details, dates,
                                    and window character. Other types: optional but
                                    useful for grounding.
        HELP('-h validate')     — REQUIRED for understanding SUBMIT. Full validation
                                    checklist and common failure modes.
        HELP('-h venue_sample') — Optional. Shows what get_venue returns.
        HELP('-h tools')        — Optional. Tool signatures (already in prompt).

        You have {max_turns} turns total — each tool call costs one turn.
        Aim to call SUBMIT by turn {max_turns - URGENCY_TURNS} at the latest.
        After reading the handbook, use THINK to plan your approach,
        explore the pool with query_pool/get_venue, and call SUBMIT.
        """
    )

    if verbose:
        print(f"  Agent loop: {model} | type={type_key} | max_turns={max_turns}")

    # Route to model-specific loop, catching any exception so we can still
    # write a debug log. Without this wrapper, a crash inside the loop
    # (e.g. malformed tool_input triggering a TypeError somewhere we haven't
    # hardened yet) escapes from run_task_agent before the transcript gets
    # written to disk — exactly the "no log produced" failure mode.
    import traceback as _traceback
    model_lower = model.lower()
    task = None
    stop_info: dict = {}
    crash_info: dict | None = None

    try:
        if "claude" in model_lower or "anthropic" in model_lower:
            if not HAS_ANTHROPIC:
                raise ImportError("anthropic package required for Claude models")
            task, stop_info = _run_anthropic_loop(
                initial_message, system_prompt, model, api_key,
                pool, pool_map, window, city, handbook, unavailable,
                verbose, type_key=type_key, max_turns=max_turns,
                _used_venue_ids=_used_venue_ids,
                _used_tension_axes=_used_tension_axes,
            )

        elif "gemini" in model_lower:
            if not HAS_GEMINI:
                raise ImportError("google-genai package required for Gemini models")
            task, stop_info = _run_gemini_loop(
                initial_message, system_prompt, model, api_key,
                pool, pool_map, window, city, handbook, unavailable,
                verbose, type_key=type_key, max_turns=max_turns,
                _used_venue_ids=_used_venue_ids,
                _used_tension_axes=_used_tension_axes,
            )

        else:
            # OpenAI + DeepSeek
            if not HAS_OPENAI:
                raise ImportError("openai package required for GPT/DeepSeek models")
            base_url = "https://api.deepseek.com/v1" if "deepseek" in model_lower else None
            messages = [{"role": "user", "content": initial_message}]
            task, stop_info = _run_openai_loop(
                messages, system_prompt, model, api_key, base_url,
                pool, pool_map, window, city, handbook, unavailable,
                verbose, type_key=type_key, max_turns=max_turns,
                _used_venue_ids=_used_venue_ids,
                _used_tension_axes=_used_tension_axes,
            )
    except Exception as _loop_exc:
        # Mid-run crash (most commonly a TypeError from malformed LLM output
        # reaching a code path we haven't hardened). Preserve whatever stop_info
        # the loop had been building, if any, and flag it as a crash.
        crash_info = {
            "exception_type": type(_loop_exc).__name__,
            "exception_msg":  str(_loop_exc),
            "traceback":      _traceback.format_exc(),
        }
        if not isinstance(stop_info, dict):
            stop_info = {}
        stop_info.setdefault("turns_used", 0)
        stop_info.setdefault("max_turns", max_turns)
        stop_info.setdefault("transcript", [])
        stop_info.setdefault("last_submit_errors", [])
        stop_info["stop_reason"] = f"crashed:{type(_loop_exc).__name__}"
        stop_info["crash"] = crash_info
        task = None

    # ── Persist transcript to disk ──────────────────────────────────────────
    # Always write agent logs — success logs are essential for token analysis
    # and constraint quality auditing, not just debugging failures.
    if task is not None:
        outcome = "success"
    elif crash_info is not None:
        outcome = "crash"
    else:
        outcome = "failure"
    should_log = True
    if should_log:
        try:
            _log_dir = Path(log_dir) if log_dir else _default_log_dir(city)
            _log_dir.mkdir(parents=True, exist_ok=True)
            # Filename: {outcome}_{model_slug}_{window}_{type}_{ts}.json
            ts          = int(time.time())
            model_slug  = model.replace("/", "_").replace(":", "_")
            type_slug   = type_key.replace("/", "_")
            window_slug = window.get("window_id", "unknown")
            fname       = f"{outcome}_{model_slug}_{window_slug}_{type_slug}_{ts}.json"
            log_path    = _log_dir / fname

            # Pop the transcript off stop_info so the caller's stop_info stays lean
            transcript = stop_info.pop("transcript", [])

            payload = {
                "outcome":       outcome,
                "model":         model,
                "type_key":      type_key,
                "city":          city,
                "window_id":     window.get("window_id"),
                "window_label":  window.get("label"),
                "max_turns":     max_turns,
                "stop_info":     stop_info,
                "initial_message": initial_message,
                "transcript":    transcript,
                "system_prompt": system_prompt[:4000] + "... [truncated]"
                                 if len(system_prompt) > 4000 else system_prompt,
            }
            log_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
            stop_info["log_path"] = str(log_path)
            if verbose or outcome != "success":
                print(f"  📝 Agent transcript saved: {log_path}")
            elif outcome == "success":
                print(f"  📝 Log: {log_path.name}")
        except Exception as _log_err:
            # Never let logging failure crash the run
            stop_info["log_error"] = str(_log_err)
            if verbose:
                print(f"  ⚠ Failed to write agent log: {_log_err}")
    else:
        # Drop transcript from returned stop_info when no log written — it's
        # already been persisted (or skipped) and callers shouldn't keep the
        # full conversation in memory for every generation.
        stop_info.pop("transcript", None)

    return task, stop_info


def _default_log_dir(city: str) -> Path:
    """Default location for agent transcript logs."""
    return Path(__file__).parent.parent.parent / "data" / "cities" / city / "tasks" / "agent_logs"