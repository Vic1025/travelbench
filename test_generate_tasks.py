"""
test_generate_tasks.py — Task generation test pipeline

Generates 2 benchmark tasks for a city using the Claude API.
Prints the full generated tasks so you can evaluate prompt quality,
persona naturalness, constraint correctness, and public_input realism.

The script shows:
  1. The venue pool the LLM sees (brief)
  2. Each generated task — full JSON + human-readable summary
  3. Solvability check result per task
  4. Any schema validation issues

Usage:
  python test_generate_tasks.py --city london --api-key sk-ant-...
  python test_generate_tasks.py --city london --api-key sk-ant-... --window london_carnival_2026
  python test_generate_tasks.py --city london --api-key sk-ant-... --count 3
  python test_generate_tasks.py --city london --dry-run   # stub only, no API
"""
import os
import json
import sys
import re as _re
import argparse
import textwrap
from pathlib import Path
from datetime import date as _date

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# VENUE POOL — loads from per-city SQLite DB
# ─────────────────────────────────────────────────────────────────────────────

def load_city_pool(city: str, db_path=None) -> list[dict]:
    """Load city venue pool — delegates to pool_utils."""
    from scripts.generation.db import get_city_db_path
    return _load_city_pool_impl(city, db_path=db_path or get_city_db_path(city))


def load_london_pool() -> list[dict]:
    """Load London pool from DB (delegates to load_city_pool)."""
    return load_city_pool("london")


def load_pool(city: str, db_path=None) -> tuple[list[dict], dict]:
    """Load pool + config — delegates to pool_utils."""
    return _load_pool_impl(city, db_path=db_path)


# ─────────────────────────────────────────────────────────────────────────────
# WINDOW
# ─────────────────────────────────────────────────────────────────────────────


def get_window_for_city(city: str, window_id: str | None,
                         db_path=None) -> dict:
    """Get window config — delegates to pool_utils."""
    from scripts.generation.db import get_city_db_path
    return _get_window_impl(city, window_id, db_path=db_path or get_city_db_path(city))


# ─────────────────────────────────────────────────────────────────────────────
# GENERATION PROMPT
# ─────────────────────────────────────────────────────────────────────────────

from scripts.generation.generate_city_venues import generate_city_interests, build_event_prompt_block
from scripts.generation.trait_registry import (
    load_registry, save_registry, register_task,
    build_registry_prompt_block, scan_existing_tasks
)
from scripts.generation.generate_task import (
    _stub_task, _verify_task_solvable
)
from scripts.generation.db import get_city_db_path
from scripts.generation.pool_utils import (
    load_city_pool as _load_city_pool_impl,
    load_pool as _load_pool_impl,
    get_window_for_city as _get_window_impl,
    build_pool_inventory as _build_pool_inventory_impl,
)



def build_pool_inventory(pool: list[dict]) -> str:
    """Build compact pool inventory — delegates to pool_utils."""
    return _build_pool_inventory_impl(pool)


def build_two_task_prompt(city: str, cfg: dict, window: dict,
                           pool: list[dict],
                           registry: dict = None) -> str:
    """
    Build a prompt that asks for exactly 2 tasks with different personas.
    Includes pool inventory so the LLM can only generate constraints
    for labels/regulations/categories that actually exist.
    """
    # Build venue pool listing
    pool_lines = []
    for v in pool[:45]:
        tags    = ", ".join(v.get("tags", [])[:5])
        flags   = ""
        if v.get("has_wrong_info"):   flags += " [WI]"
        if v.get("family_friendly"):  flags += " [FAM]"
        if v.get("booking_required"): flags += " [BOOK]"
        if v.get("wheelchair_accessible"): flags += " [WC]"
        pool_lines.append(
            f"  {v['venue_id']} | {v['name']} | {v['category']} | "
            f"{v.get('district','?')} | {v.get('traffic_tier','?')} | "
            f"{v.get('recommended_pace','?')} | tags: {tags}{flags}"
        )

    pool_text  = "\n".join(pool_lines)
    inventory  = build_pool_inventory(pool)
    reg_block  = build_registry_prompt_block(registry) if registry else                  "TRAIT REGISTRY: (none yet — all traits available)"

    # City visitor interests — only shown for types where Cat 6 is relevant
    _INTEREST_TYPES = {"type1", "type2", "type3", "type6"}
    if interests and type_key in _INTEREST_TYPES:
        interest_lines = "\n".join(f"  - {i}" for i in interests)
        interest_block = (
            f"CITY VISITOR INTERESTS — why people visit {cfg.get('display_name', city)}:\n"
            f"{interest_lines}\n"
            "When using a Cat 6 interest signal, pick from this list. "
            "Do not invent generic interests like 'photography' if they don't fit the city context."
        )
    else:
        interest_block = ""

    dates      = window.get("dates", [])
    date_range = f"{dates[0]} – {dates[-1]}" if dates else ""
    anchor_str = ", ".join(
        f"{e['name']} ({e['date']})"
        for e in window.get("anchor_events", [])
    )

    return f"""Generate exactly TWO benchmark tasks for:

CITY: {cfg['display_name']}, {cfg['country']}
WINDOW: {window['label']} ({date_range})
ANCHOR EVENTS: {anchor_str}

WINDOW CHARACTER:
{window['character']}

CONDITIONAL WRONG INFO TRAPS:
{json.dumps(window.get('conditional_wrong_info_hints', []), indent=2)}

{reg_block}

{interest_block}

{inventory}

VENUE POOL ({len(pool)} venues):
Format: venue_id | name | category | district | traffic_tier | pace | tags | [WI=wrong_info, FAM=family, BOOK=booking_required, WC=wheelchair]
{pool_text}

STRUCTURAL TYPE REFERENCE (what makes each type hard — use this to pick):

type1_hidden_requirements
  Difficulty: agent must INFER implicit needs before solving. User query hides the constraints.
  Hard via: cascade 2-3 implicit needs from one signal (child → max_visit_duration + noise_level_max + meal timing)
  P-score: regulation_required, noise_level_max, max_visit_duration, time_threshold, pace_relaxed

type2_subset_selection
  Difficulty: MORE options exist than fit. Hard ceiling (time, not budget) forces choosing best subset.
  Hard via: ceiling must BITE — pool has 7 museums, days allow 3, sequence matters
  P-score: category_count_minimum, label_required (sets preference), time_threshold (ceiling)

type3_competing_requirements
  Difficulty: two signals pull at DIFFERENT parts of the pool, impossible to fully satisfy both.
  Hard via: hidden-gem (low-traffic) vs iconic (high-traffic) are real opposites in the pool
  P-score: hidden_gem_required + label_required:iconic + category_count_minimum for both sides

type4_precision_allocation
  Difficulty: tight budget requires intelligent distribution, not just staying under ceiling.
  Hard via: budget must actually be tight relative to pool costs; free venues must exist and matter
  P-score: numeric_aggregate (budget ≤ X/day) + price_tier_required:upscale for one thing (tension)

type5_hard_feasibility
  Difficulty: stacking ≥3 filters leaves ≤3 viable venues. Agent must find the narrow intersection.
  Hard via: wheelchair + halal + family_friendly together — check pool: how many satisfy ALL THREE?
  P-score: regulation_required + label_required (dietary/family) + possibly district_count_max

type6_context_window_tension
  Difficulty: persona preference conflicts with the SEASONAL WINDOW character. Needs both.
  Hard via: conflict must be SPECIFIC to the window dates — "outdoor photography" + Christmas sunset
  P-score: time_threshold (outdoor before 16:00), label_excluded (crowded during event), district_count_max

Compatible secondary pairs: type1+type4, type2+type3, type5+type6

TASK REQUIREMENTS:

Generate 2 tasks. You choose the structural types, difficulty, and personas.
Rules:
- The two tasks MUST have different structural types and different persona categories.
- At least one task must be HARD difficulty.
- Together they should exercise different parts of the constraint space —
  one lighter/inference-based, one with genuine feasibility pressure.

Don't default to type1 for both. Prefer type2, type3, type4, type5, type6.

PERSONA DIVERSITY (mandatory — within this run):
- CHARACTER TRAIT CAP: ≤2 character trait categories per task.
  Character traits = Cat 2 (dietary/physical) + Cat 5 (authenticity) + Cat 6 (interests) + Cat 7 (occasion).
  Cat 1 (composition), Cat 3 (schedule), Cat 4 (budget), Cat 8 (logistics) do NOT count.
  Exception: Type 5 allows up to 4 character traits (stacking IS the mechanism).
- WITHIN-TASK Cat 2 RULE: at most ONE physical/dietary (Cat 2) signal per task.
    "Vegetarian" alone is fine. "Vegetarian + wheelchair" is two Cat 2 signals = NOT fine.
    Exception: Type 5 may stack multiple Cat 2 signals — the pool narrowing IS the point.
- SAME-CLUSTER RULE: never two signals from the same sub-cluster:
    dietary: vegetarian / vegan / halal / kosher / gluten-free → pick ONE.
    Cat 5 seek_hidden: "hidden gems" and "no tourist traps" are the same signal → pick ONE.
- NO OVERLAP across tasks: same main trait may not appear in both tasks.
    ✗ vegetarian in task 1 AND vegan in task 2  (same dietary cluster)
    ✗ photographer in both tasks
    ✗ anniversary in both tasks
    ✗ hidden gems in both tasks
- If one task uses a Cat 2 signal, the other should use Cat 3, Cat 4, or Cat 8 instead.
- Good contrasting pairs:
    Task 1: Cat 7 (anniversary) + Cat 5 (no tourist traps)
    Task 2: Cat 3 (flight at noon — time ceiling) + Cat 4 (tight budget)
    or
    Task 1: Cat 2 (halal) + Cat 8 (staying in one district)
    Task 2: Cat 6 (live music) + Cat 7 (birthday)

SHARED RULES:
- public_input: ONE field only: "query" — 2-4 natural first-person sentences.
  NO user_profile field. NO constraint language. Write like a real chatbot message.
  GOOD: "My mum uses a wheelchair and can't manage more than an hour at each stop."
  BAD: "regulation_required: wheelchair_accessible, max_visit_duration: 60"
- start_date must be from: {', '.join(dates)}
- Task IDs: {cfg['city'][:3]}_gen_001 and {cfg['city'][:3]}_gen_002
- Days: 1-3 days, your choice based on what fits the scenario.

HOP-2 RULE (mandatory for both tasks):
  Each task must have ≥1 hop-2 P-constraint (requires reasoning, not direct label read).
  hop-1: "vegetarian" → label_required: vegetarian  (obvious direct read)
  hop-2: "grandmother gets tired" → max_visit_duration: 60min  (requires inference)
  hop-2: "staying near République" → district_count_max: 2  (requires inference)
  hop-2: "toddler naps at 13:00" → no activities 12:30-14:30  (requires inference)

CONSTRAINT TENSION RULE (mandatory for both tasks):
  Each task must have ≥2 P-constraints that interact — satisfying both narrows the pool.
  Good pairs: budget + upscale meal, wheelchair + outdoor, hidden-gem + museum-count,
  family-friendly + late dinner, district-limit + category-minimum.

CRITICAL FIELD NAMES:
- personal_constraints use "pattern" NOT "type"
- Every personal_constraint MUST have ALL of:
    id, score_tier ("P"), hop (1 or 2), pattern, check_method ("code"),
    source_in_profile, description, params
- Every b_score_constraint MUST have:
    id, score_tier ("B"), hop (3), pattern, description
  + llm_semantic: also rubric_prompt and scoring_guide
  + python_script: also script_code with "def evaluate(plan, task, venues) -> float"

python_script template:
  "def evaluate(plan, task, venues):\\n  acts = [a for d in plan.get('days',[]) for a in d.get('activities',[]) ]\\n  # your specific check\\n  return 1.0"

Return a JSON array of exactly 2 task objects. No markdown, no preamble."""


# ─────────────────────────────────────────────────────────────────────────────
# LLM CALL
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# PER-TYPE SPECIALIZED PROMPTS
# One call per structural type. Each prompt only shows what that type needs.
# ─────────────────────────────────────────────────────────────────────────────

_TYPE_SPECS = {
"type1": dict(
    name="type1_cascading_requirements",
    difficulty="easy or medium", days="1 or 2",
    persona_cats="Cat 1 (composition) + Cat 7 (occasion)",
    description="""The query contains a persona combination that naturally cascades into
multiple interacting constraints. Signals ARE stated in the query — the agent hears them —
but hop-2 reasoning is needed to translate each into the correct constraint.
The cascade is the difficulty: 2-3 stated signals → 4-5 derived constraints.""",
    hop_rule="""EVERY hop-2/3 constraint must have a matching natural signal in the query.
The agent needs raw material to reason from — they should not need to guess what dimension matters.
BAD: query says "couple" → constraint max_visit_duration (no signal → nothing to reason from)
GOOD: query says "partner gets tired after a couple of hours" → max_visit_duration (signal present)""",
    example_query=""""It's my dad's 70th birthday and I've planned a weekend trip to celebrate.
He gets tired easily these days and can't stand being on his feet for more than
an hour or so. I want at least one really special dinner on the actual birthday evening."

Signal → constraint cascade:
  "dad's 70th birthday"             → occasion: celebration (hop-1 Cat 7)
  "gets tired, can't stand long"    → max_visit_duration: 60min (hop-2 P — infer bound)
  "birthday evening"                → dinner ≥ 19:00, price_tier upscale (hop-2 P)
  "can't stand long" + full day     → pace_relaxed, rest window mid-day (hop-2 P)
  birthday + milestone + 70th       → arc builds to dinner as centrepiece (hop-3 B)

Another example (different composition):
  "Three of us on a long weekend — first time we've all been in the same city in years.
  We're all big history nerds and want to cram in as much as possible, but we're also
  celebrating Jess's new job so at least one evening needs to feel special."

Signal → constraint cascade:
  "three of us, first time together" → group of 3 (Cat 1)
  "history nerds"                    → label_required: history (hop-1 Cat 6 interest)
  "cram in as much as possible"      → category_count_minimum (hop-1 Cat 3)
  "Jess's new job, special evening"  → occasion: celebration → time_threshold + price_tier (hop-2)
  cramming + special evening         → arc tension: pace vs occasion peak (hop-3 B)""",
    constraints="""P-score: regulation_required (family_friendly), noise_level_max,
  max_visit_duration, time_threshold (dinner ≥ 18:00), pace_relaxed""",
    persona_guidance="""Use Cat 1 + Cat 7 together. The occasion interacts with composition.
  Vary the Cat 1 composition — avoid defaulting to "adult + child" every time:
    Solo traveller + exhausting week → pace + schedule signals, no crowds
    Retiree couple + milestone anniversary → energy limits + formality + timing
    Two adult siblings + one's birthday → group dynamics + occasion arc
    Parent + 10-year-old + birthday → duration + noise + celebration arc
    Single parent + young child → crowds + geography + rest window (use sparingly)
  The occasion (Cat 7) should change the *character* of the day, not just add a dinner.
Do NOT use Cat 2 for Type 1 — dietary/physical are direct hop-1 reads, not cascades.""",
),

"type2": dict(
    name="type2_subset_selection",
    difficulty="medium or hard", days="1 or 2",
    persona_cats="Cat 3 (schedule/rhythm) or Cat 8 (logistics) + Cat 6 (interests)",
    description="""More valid options exist than fit. A hard time ceiling forces choosing
the best subset and sequencing it. The ceiling must actually bite —
pool has N candidates, ceiling fits M < N.""",
    hop_rule="""The ceiling signal must be stated in the query (arrival time, departure time,
one free evening). The agent computes the effective window from what the user said.
Hop-2: agent must infer the time constraint from a stated logistics signal.""",
    example_query=""""I arrive in Paris at 14:00 Saturday and fly out Sunday at 10:00 — basically
one afternoon and evening. I love contemporary art and want to fit in as many
galleries as possible. Also want dinner somewhere with good wine."

Signal → constraint cascade:
  "arrive 14:00, fly 10:00"        → time ceiling: ~18 effective hours (hop-1 ceiling)
  "as many galleries as I can"     → category_count_minimum: museum ≥ 3 (hop-1 target)
  "contemporary art"               → label_required: contemporary-art (hop-1 filter)
  "dinner + good wine"             → label_required: wine-bar (hop-1 qualifier)
  one afternoon/evening only       → must sequence geographically (hop-2 B — infer from tight schedule)""",
    constraints="""P-score: time_threshold (ceiling from logistics), category_count_minimum,
  label_required (interest filter), district_count_max""",
    persona_guidance="""Cat 3 or Cat 8 creates the ceiling. Cat 6 interest shapes the selection.
  Arriving late / leaving early → time ceiling Type 2
  "One free evening in the city" → evening-only Type 2
The ceiling signal must appear in the query. Agent must compute the window from it.""",
),

"type3": dict(
    name="type3_competing_requirements",
    difficulty="medium or hard", days="2 or 3",
    persona_cats="Cat 5 (authenticity) vs Cat 6 (interests), or two people with different preferences",
    description="""Two stated preferences pull at genuinely different parts of the pool.
No plan fully satisfies both. Agent must find a genuine tradeoff.
hidden-gem (low-traffic) vs iconic (high-traffic) are real opposites in the pool.""",
    hop_rule="""Both competing signals must be clearly stated in the query — they come from
different people or explicitly stated competing desires. The tension should be
acknowledged in the query itself. Hop-3: neither preference should dominate.""",
    example_query=""""My partner wants all the famous Paris sights since it's her first time,
but I've been before and really want to find places that feel local and off the
beaten path. We need a balance that works for both of us."

Signal → constraint cascade:
  "famous sights, first time"       → label_required: iconic, min 2 (hop-1 P — her preference)
  "local and off beaten path"       → hidden_gem_required, min 2 (hop-1 P — his preference)
  "been before / first time"        → genuinely competing desires, neither can dominate
  "balance that works for both"     → 25-75% ratio check (hop-3 B — infer from "balance")""",
    constraints="""P-score: hidden_gem_required (min 2), label_required: iconic (min 2),
  category_count_minimum for weaker side, district_count_max""",
    persona_guidance="""Competing signals must come from different stated desires — either two
people with different needs, or one person explicitly torn between two things.
Cat 5 vs Cat 6 is canonical. Cat 7 occasion vs Cat 1 group also works.
The query should name the tension, not just list two preferences.""",
),

"type4": dict(
    name="type4_precision_allocation",
    difficulty="medium or hard", days="2 or 3",
    persona_cats="Cat 4 (budget) + Cat 7 (occasion) or Cat 6 (interests)",
    description="""A stated tight budget requires intelligent distribution.
Free venues must exist and matter. One justified high-value spend is implied
by a second stated signal — the tension is allocating to afford it.""",
    hop_rule="""Budget must be a specific stated number. The tension signal (occasion/interest
that justifies one expensive thing) must also be stated. Hop-2: agent must infer
WHICH thing to allocate toward, not just stay under a ceiling.""",
    example_query=""""I'm a student doing Paris on €45 a day max for everything. But it's also
my girlfriend's birthday on the second day, so I want to make that evening feel
genuinely special even on a tight budget."

Signal → constraint cascade:
  "€45 a day max"                  → numeric_aggregate ≤ 45/day (hop-1 P — direct)
  "student, shoestring"            → prefer free-entry venues (hop-1 P)
  "birthday, second day"           → one upscale dinner on day 2 (hop-2 P — occasion justifies exception)
  tight budget + special evening   → free venues on day 2 daytime to save for dinner (hop-3 B)""",
    constraints="""P-score: numeric_aggregate (budget ≤ X/day), label_required: free-entry,
  price_tier_required: upscale for ONE meal on occasion day""",
    persona_guidance="""Budget (Cat 4) must be specific — a number. Cat 7 occasion or Cat 6
interest creates the allocation tension: the one thing they want to spend on.
Without the tension signal it's just "stay under budget" — no interesting allocation.""",
),

"type5": dict(
    name="type5_hard_feasibility",
    difficulty="hard", days="2 or 3",
    persona_cats="Cat 1 (composition) + Cat 2 (physical/dietary) + Cat 8 (logistics)",
    description="""Stacking multiple stated filters from different categories leaves only
a narrow viable set. The agent must find which venues satisfy ALL simultaneously.
This is the ONE type where multiple Cat 2 signals are allowed.
VERIFY with pool inventory: intersection must yield ≥1 venue.""",
    hop_rule="""All constraint signals must be clearly stated in the query. The narrowness
is the puzzle — not discovering what constraints exist, but finding what's left.
Hop-2: district constraint from staying location. Hop-3: verifying intersection.""",
    example_query=""""Visiting with my elderly mother who uses a wheelchair and eats halal.
We're staying near Canal Saint-Martin and she gets tired after an hour or so —
we can't really travel far between places."

Signal → constraint cascade:
  "uses a wheelchair"               → regulation_required: wheelchair_accessible (hop-1 F)
  "eats halal"                      → label_required: halal (hop-1 P)
  "staying near Canal Saint-Martin" → district_count_max: 2 (hop-2 P — infer geography)
  "gets tired after an hour"        → max_visit_duration: 60min (hop-2 P — infer bound)
  wheelchair + halal + district     → only 1-3 venues satisfy all three (hop-3 B verify)""",
    constraints="""P-score: regulation_required (wheelchair_accessible), label_required (halal),
  district_count_max, max_visit_duration""",
    persona_guidance="""Stack 3+ filters from different clusters. Each alone is easy — together they narrow.
  Vary the composition — many stacking scenarios don't involve children at all:
    Retiree + wheelchair + halal + one district → mobility + dietary + geography
    Large group (6+) + vegetarian + booking required → capacity + dietary + logistics
    Solo traveller + dog + outdoor + quiet → pet + noise + pace
    Multi-generational + wheelchair + budget → mobility + budget + district
    Family with child + nut allergy → age restriction + allergy (use sparingly)
Check pool inventory: does the intersection actually yield 1-3 venues? If 0, relax one.""",
),

"type6": dict(
    name="type6_context_window_tension",
    difficulty="medium or hard", days="1 or 2",
    persona_cats="Cat 7 (occasion) + seasonal window anchor events",
    description="""A stated persona preference conflicts with the window's specific character.
The conflict must be SPECIFIC to the window dates — not generic timing but
this anchor event, these closures, this crowd surge.""",
    hop_rule="""The query must reference the date or event naturally (as a real traveller would).
Hop-2: agent must recognise the conflict between what the user wants and what the window makes hard.
Without a specific window conflict, use Type 3 instead.""",
    example_query=""""We're in Paris on Ascension Day for our anniversary. We'd love a romantic
dinner somewhere special that evening, but we've heard some places close for the
holiday — can you help us plan the day around that?"

Signal → constraint cascade:
  "Ascension Day" + "anniversary"   → occasion on a public holiday (hop-1 context signal)
  "romantic dinner that evening"    → time_threshold: dinner ≥ 19:00 + price_tier: upscale (hop-2 P)
  "some places close"               → agent must call get_official_site with anchor date (hop-2 B)
  "plan the day around that"        → day activities before dinner, avoiding closed venues (hop-3 B)""",
    constraints="""P-score: time_threshold (dinner ≥ 19:00 on anchor date),
  label_excluded: tourist (holiday crowds), price_tier_required: upscale""",
    persona_guidance="""Cat 7 occasion is the persona. The window anchor event is the conflict.
The query must name the date or event — real travellers mention what they know about the dates.
The tension: occasion requires X (anniversary dinner), window makes X hard (holiday closures).""",
),
}



# ─────────────────────────────────────────────────────────────────────────────
# CAT 1 COMPOSITION QUOTA SYSTEM (percentage-based, scales with total tasks)
# ─────────────────────────────────────────────────────────────────────────────

CAT1_OPTIONS: dict[str, str] = {
    "solo":          "solo traveller",
    "couple":        "couple (two adults, romantic or otherwise)",
    "friends_small": "group of 3-5 friends",
    "child":         "adult with young child (under 12)",
    "siblings":      "two adults (siblings, old friends, or colleagues)",
    "retirees":      "retiree couple or two older adults",
    "teenagers":     "family with teenagers (13-17)",
    "solo_woman":    "solo woman traveller",
    "single_parent": "single parent with child",
    "multi_gen":     "multi-generational group (grandparent + adult + child)",
    "friends_large": "group of 6+ (stag/hen party, work team, school trip)",
    "dog":           "travelling with dog",
    "colleagues":    "work colleagues on a team outing",
}

# Proportion of total tasks each composition may appear in (upper bound quota).
# Sums to >1.0 — these are caps, not allocations. 5% of tasks stay unassigned.
CAT1_QUOTAS: dict[str, float] = {
    "solo":          0.13,
    "couple":        0.13,
    "friends_small": 0.13,
    "child":         0.13,
    "siblings":      0.10,
    "retirees":      0.10,
    "teenagers":     0.10,
    "solo_woman":    0.07,
    "single_parent": 0.07,
    "multi_gen":     0.07,
    "friends_large": 0.03,
    "dog":           0.03,
    "colleagues":    0.03,
}
CAT1_FREE_PCT = 0.05   # 5% of tasks have no assigned composition

# Compatibility: which compositions work for each structural type
CAT1_COMPAT: dict[str, list[str]] = {
    "type1": ["solo","couple","siblings","retirees","solo_woman","child","single_parent","teenagers"],
    "type2": ["solo","couple","friends_small","friends_large","colleagues","solo_woman","siblings"],
    "type3": ["couple","friends_small","siblings","friends_large","teenagers","colleagues"],
    "type4": ["solo","couple","friends_small","solo_woman","retirees","siblings"],
    "type5": ["multi_gen","friends_large","colleagues","child","dog","retirees","single_parent"],
    "type6": ["couple","solo","friends_small","solo_woman","retirees","siblings","single_parent"],
}


def compute_cat1_quotas(n_tasks: int) -> dict[str, int]:
    """Compute integer quotas for each composition given total task count."""
    return {k: max(1, round(pct * n_tasks)) for k, pct in CAT1_QUOTAS.items()}


def build_cat1_slot_block(type_key: str,
                           used_counts: dict[str, int],
                           quotas: dict[str, int],
                           n_tasks: int,
                           cat1_override: str | None = None) -> str:
    """
    Build the COMPOSITION SLOT block shown in each per-type prompt.
    Displays available compositions (under quota + compatible) and their usage counts.
    """
    if cat1_override and cat1_override in CAT1_OPTIONS:
        label = CAT1_OPTIONS[cat1_override]
        return (
            "COMPOSITION SLOT (forced by --cat1 flag):\n"
            f"  USE: {label}\n"
            "  Build all query signals and constraints around this traveller type."
        )

    compat    = CAT1_COMPAT.get(type_key, list(CAT1_OPTIONS.keys()))
    available = [k for k in compat if used_counts.get(k, 0) < quotas.get(k, 1)]
    exhausted = [k for k in compat if used_counts.get(k, 0) >= quotas.get(k, 1)]

    free_slots     = max(1, round(n_tasks * CAT1_FREE_PCT))
    free_used      = used_counts.get("__free__", 0)
    free_remaining = max(0, free_slots - free_used)

    lines = ["COMPOSITION SLOT — pick ONE from the available list:"]
    lines.append("")
    lines.append("Available (under quota, compatible with this type):")
    for k in available:
        used  = used_counts.get(k, 0)
        quota = quotas.get(k, 1)
        lines.append(f"  ✓ {k:15s}  [{used}/{quota} used]  {CAT1_OPTIONS[k]}")

    if exhausted:
        lines.append("")
        lines.append("Quota reached — do NOT use:")
        for k in exhausted:
            lines.append(f"  ✗ {k:15s}  [{used_counts.get(k,0)}/{quotas.get(k,1)} used]")

    if free_remaining > 0:
        lines.append("")
        lines.append(f"Free slot ({free_remaining}/{free_slots} remaining in this run):")
        lines.append("  May use any composition not listed above, or an uncommon one")
        lines.append("  (e.g. exchange student, honeymooners, attending a wedding).")

    lines.append("")
    lines.append("Your choice anchors the entire query, persona, and constraint set.")
    return "\n".join(lines)
def build_typed_task_prompt(type_key: str, city: str, cfg: dict, window: dict,
                             pool: list[dict], registry: dict = None,
                             task_num: int = 1, total_tasks: int = 6,
                             interests: list[str] = None,
                             event_briefs: list[dict] | None = None,
                             venue_briefs: list[dict] | None = None,
                             cat1_override: str | None = None,
                             used_counts: dict[str, int] | None = None,
                             quotas: dict[str, int] | None = None,
                             n_tasks: int = 6,
                             unavailable: dict | None = None,
                             centre_lat: float | None = None) -> str:
    """
    Build a specialized prompt for ONE structural type.
    unavailable: {venue_id: [sold_out_date, ...]} for this window.
    centre_lat: city centre latitude for lng_factor calculation.
    """
    import math as _math
    unavailable = unavailable or {}

    # Compute lng_factor from centre_lat (derive from pool if not given)
    if centre_lat is None:
        hi = [(v["lat"], v["lng"]) for v in pool
              if v.get("traffic_tier") == "high" and v.get("lat") and v.get("lng")]
        if not hi:
            hi = [(v["lat"], v["lng"]) for v in pool if v.get("lat") and v.get("lng")]
        centre_lat = sum(c[0] for c in hi) / len(hi) if hi else 48.8
    lng_factor = round(_math.cos(_math.radians(centre_lat)) * 111, 1)

    spec = _TYPE_SPECS[type_key]
    composition_block = build_cat1_slot_block(
        type_key, used_counts or {}, quotas or {}, n_tasks or 6,
        cat1_override=cat1_override
    )

    # Events block — only for types that benefit from window event context
    _EVENT_TYPES = {"type2", "type5", "type6"}
    window_dates = window.get("dates", []) if window else []
    if event_briefs and venue_briefs and type_key in _EVENT_TYPES:
        event_block = build_event_prompt_block(event_briefs, venue_briefs, window_dates)
    else:
        event_block = ""

    # Venue pool listing with coordinates and sold-out flags
    pool_lines = []
    for v in pool[:45]:
        tags  = ", ".join(v.get("tags", [])[:5])
        flags = ""
        if v.get("has_wrong_info"):        flags += " [WI]"
        if v.get("family_friendly"):       flags += " [FAM]"
        if v.get("booking_required"):      flags += " [BOOK]"
        if v.get("wheelchair_accessible"): flags += " [WC]"
        lat, lng = v.get("lat"), v.get("lng")
        coord_str = f" | {lat:.3f},{lng:.3f}" if lat is not None and lng is not None else " | ?,?"
        sold = unavailable.get(v["venue_id"], [])
        sold_str = f" [SOLD:{','.join(sold)}]" if sold else ""
        pool_lines.append(
            f"  {v['venue_id']} | {v['name']} | {v['category']} | "
            f"{v.get('district','?')} | {v.get('traffic_tier','?')} | "
            f"{v.get('recommended_pace','?')} | tags:{tags}{coord_str}{flags}{sold_str}"
        )
    pool_text = "\n".join(pool_lines)
    inventory = build_pool_inventory(pool)
    reg_block = build_registry_prompt_block(registry) if registry else \
                "TRAIT REGISTRY: (none yet — all traits available)"

    dates      = window.get("dates", [])
    date_range = f"{dates[0]} – {dates[-1]}" if dates else ""
    anchor_str = ", ".join(
        f"{e['name']} ({e['date']})"
        for e in window.get("anchor_events", [])
    )

    return f"""Generate 1 benchmark task for TravelBench.

CITY: {cfg['display_name']}, {cfg['country']}
WINDOW: {window['label']} ({date_range})
ANCHOR EVENTS: {anchor_str}

WINDOW CHARACTER:
{window['character']}

CONDITIONAL WRONG INFO TRAPS:
{json.dumps(window.get('conditional_wrong_info_hints', []), indent=2)}

TRAVEL FORMULA (lng_factor={lng_factor} for this city):
dist_km ≈ sqrt((Δlat×111)² + (Δlng×{lng_factor})²)  walk=dist×12min  transit=dist×8+5min
[WI]=wrong info  [SOLD:date,...]=sold-out on those dates

{reg_block}

{("" + event_block + chr(10)) if event_block else ""}{inventory}

VENUE POOL ({len(pool)} venues):
Format: venue_id | name | category | district | traffic_tier | pace | tags | lat,lng | [flags]
{pool_text}

═══════════════════════════════════════════════════
STRUCTURAL TYPE: {spec['name']}
Task {task_num} of {total_tasks}
═══════════════════════════════════════════════════

WHAT MAKES THIS TYPE HARD:
{spec['description']}

HOP-2/3 SIGNAL RULE:
{spec['hop_rule']}

{composition_block}

RELEVANT PERSONA CATEGORIES FOR THIS TYPE:
{spec['persona_cats']}

{spec['persona_guidance']}

EXAMPLE QUERY AND CONSTRAINT CASCADE:
{spec['example_query']}

CONSTRAINTS TO GENERATE FOR THIS TYPE:
{spec['constraints']}

YOUR TASK:
- Difficulty: {spec['difficulty']}
- Days: {spec['days']}
- start_date: choose from {", ".join(dates)}
- task_id: any placeholder — auto-generated from model/city/type/timestamp on SUBMIT
- Structural type MUST be: {spec['name']}

QUERY RULE: public_input has ONE field: "query" — 2-4 natural first-person sentences.
  NO user_profile. NO constraint language. Every hop-2/3 signal must appear naturally.

FIELD NAMES:
- personal_constraints use "pattern" NOT "type"
- Every personal_constraint: id, score_tier ("P"), hop, pattern, check_method ("code"),
  source_in_profile (exact phrase from query), description, params
- Every b_score_constraint: id, score_tier ("B"), hop (3), pattern, description
  + llm_semantic: rubric_prompt, scoring_guide
  + python_script: script_code with "def evaluate(plan, task, venues) -> float"

Return ONLY valid JSON for a single task object. No markdown, no explanation."""



def generate_all_types(city: str, cfg: dict, window: dict,
                        pool: list[dict], api_key: str,
                        model: str = "claude-sonnet-4-20250514",
                        registry: dict = None,
                        type_keys: list[str] = None,
                        interests: list[str] = None,
                        cat1_override: str | None = None,
                        used_counts: dict[str, int] | None = None,
                        quotas: dict[str, int] | None = None,
                        n_tasks: int = 6,
                        event_briefs: list[dict] | None = None,
                        venue_briefs: list[dict] | None = None,
                        unavailable: dict | None = None,
                        centre_lat: float | None = None,
                        max_turns: int | None = None,
                        log_dir: Path | None = None,
                        verbose: bool = False) -> list[dict]:
    """
    Generate one task per structural type (6 total by default).
    Uses agent loop. Returns list of parsed task dicts.
    """
    from scripts.generation.db import get_city_db_path
    all_type_keys = ["type1","type2","type3","type4","type5","type6"]
    keys  = type_keys or all_type_keys
    total = len(keys)
    tasks = []
    _used = used_counts if used_counts is not None else {}
    _quot = quotas or compute_cat1_quotas(n_tasks)

    for i, type_key in enumerate(keys, 1):
        spec = _TYPE_SPECS[type_key]
        print(f"\n  [{i}/{total}] Generating {spec['name']}...")

        # Agent loop path (default)
        try:
            from scripts.generation.task_agent import run_task_agent
            from scripts.generation.task_agent import MAX_TURNS as _DEFAULT_MAX_TURNS
            task, stop_info = run_task_agent(
                city=city,
                window=window,
                pool=pool,
                type_key=type_key,
                model=model,
                api_key=api_key,
                db_path=get_city_db_path(city, run_name=getattr(args,'run_name',None) if 'args' in dir() else None),
                unavailable=unavailable or {},
                centre_lat=centre_lat,
                max_turns=max_turns if max_turns is not None else _DEFAULT_MAX_TURNS,
                verbose=verbose,
                log_dir=log_dir,
            )
            turns_used = stop_info.get("turns_used", "?")
            max_t      = stop_info.get("max_turns", "?")
            reason     = stop_info.get("stop_reason", "unknown")

            if task is None:
                # Build a readable diagnostic instead of the old generic
                # "exhausted turns without valid SUBMIT" message.
                reason_msg = {
                    "exhausted":     f"turns exhausted ({turns_used}/{max_t})",
                    "no_tool_calls": (f"agent refused to call SUBMIT "
                                      f"(stopped at turn {turns_used}/{max_t}) — "
                                      f"wrote response as prose instead of using the tool"),
                }.get(reason, f"{reason} (stopped at turn {turns_used}/{max_t})")
                print(f"  ❌ Agent failed: {reason_msg}")

                # If the agent tried SUBMIT but validation kept failing,
                # surface the last error so it's easier to diagnose.
                last_errors = stop_info.get("last_submit_errors", [])
                if last_errors:
                    print(f"     Last SUBMIT errors ({len(last_errors)}):")
                    for err in last_errors[:3]:
                        print(f"       - {err[:200]}")

                # Surface the transcript log path so the user can inspect
                # exactly what the agent did on this failed run.
                log_path = stop_info.get("log_path")
                if log_path:
                    print(f"     📝 Full transcript: {log_path}")

                tasks.append({
                    "_error":          reason,
                    "_turns_used":     turns_used,
                    "_max_turns":      max_t,
                    "_last_errors":    last_errors,
                    "_log_path":       log_path,
                    "structural_type": spec["name"],
                })
            else:
                task["structural_type"] = spec["name"]
                task.setdefault("city", city)
                task.setdefault("window_id", window["window_id"])
                task["_turns_used"] = turns_used
                tasks.append(task)
                print(f"  ✓ Generated: {task.get('task_id','?')} "
                      f"({task.get('difficulty','?')}) in {turns_used}/{max_t} turns")
        except Exception as e:
            print(f"  ❌ Failed: {e}")
            tasks.append({"_error": str(e), "structural_type": spec["name"]})

    return tasks


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITION DETECTION — maps a saved task back to a CAT1_OPTIONS key
# ─────────────────────────────────────────────────────────────────────────────

# Keywords that signal each composition in the query text
_COMP_SIGNALS: dict[str, list[str]] = {
    "child":         ["young child", "year-old", "kid", "daughter", "son",
                      "toddler", "little one", "primary school"],
    "teenagers":     ["teenager", "teen", "13-year", "14-year", "15-year",
                      "16-year", "17-year", "high school"],
    "single_parent": ["single parent", "just the two of us, me and my"],
    "multi_gen":     ["grandparent", "grandmother", "grandfather", "multi-gen",
                      "three generations", "gran", "grandma", "grandpa"],
    "friends_large": ["group of 6", "group of 7", "group of 8", "group of 9",
                      "stag", "hen party", "hen do", "team of", "school trip",
                      "large group", "six of us", "seven of us", "eight of us"],
    "friends_small": ["group of 3", "group of 4", "group of 5", "three friends",
                      "four friends", "five friends", "three of us", "four of us",
                      "five of us", "small group"],
    "colleagues":    ["colleague", "work trip", "team outing", "team trip",
                      "work colleagues", "offsite", "company trip"],
    "dog":           ["dog", "pet", "four-legged", "pup", "canine"],
    "retirees":      ["retired", "retiree", "retirement", "older adult",
                      "in our 60s", "in our 70s", "senior"],
    "solo_woman":    ["solo woman", "woman travelling alone", "travelling alone as a woman",
                      "female solo", "solo female"],
    "couple":        ["partner", "girlfriend", "boyfriend", "husband", "wife",
                      "fiancé", "fiancee", "anniversary", "honeymoon", "romantic",
                      "just the two of us", "my other half"],
    "siblings":      ["brother", "sister", "sibling", "two of us",
                      "old friend", "childhood friend"],
    "solo":          ["solo", "by myself", "on my own", "travelling alone",
                      "just me", "i'm visiting", "i am visiting"],
}

# Specificity order — more specific compositions checked first to avoid false matches
_COMP_ORDER = [
    "single_parent", "multi_gen", "friends_large", "colleagues",
    "teenagers", "child", "dog", "solo_woman", "retirees",
    "friends_small", "couple", "siblings", "solo",
]


def _detect_composition(task: dict) -> str | None:
    """
    Detect which CAT1_OPTIONS key the task most likely uses, based on query text.
    Returns the key string, or None if no clear signal (free slot).
    """
    _pi_raw = task.get("public_input", {})
    if isinstance(_pi_raw, str):
        try:
            import json as _jj; _pi_raw = _jj.loads(_pi_raw)
        except Exception:
            _pi_raw = {}
    query = (_pi_raw.get("query", "") or "").lower()
    if not query:
        return None
    for key in _COMP_ORDER:
        signals = _COMP_SIGNALS.get(key, [])
        if any(sig in query for sig in signals):
            return key
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PRINTER — human-readable task summary
# ─────────────────────────────────────────────────────────────────────────────

def print_task_summary(task: dict, pool: list[dict], index: int):
    """Print a human-readable summary of a generated task."""
    pool_map = {v["venue_id"]: v for v in pool}

    print(f"\n{'═'*60}")
    print(f"TASK {index}: {task.get('task_id','?')}  "
          f"({str(task.get('difficulty','?')).upper()}, {task.get('days','?')} day(s))")
    print(f"{'═'*60}")

    pi = task.get("public_input", {})
    print(f"\n  QUERY:")
    print(textwrap.fill(pi.get("query",""), width=70, initial_indent="    ",
                        subsequent_indent="    "))
    print(f"\n  USER PROFILE:")
    print(textwrap.fill(pi.get("user_profile",""), width=70, initial_indent="    ",
                        subsequent_indent="    "))

    print(f"\n  STRUCTURAL TYPE:  {task.get('structural_type','?')}")
    if task.get("structural_type_secondary"):
        print(f"  SECONDARY TYPE:   {task['structural_type_secondary']}")
    print(f"  WINDOW:           {task.get('window_id','?')}")
    print(f"  START DATE:       {task.get('start_date','?')} ({task.get('start_day_of_week','?')})")

    rubric = task.get("rubric", {})
    hcs    = rubric.get("hard_constraints", [])
    pcs    = rubric.get("personal_constraints", [])
    bcs    = rubric.get("b_score_constraints", [])

    print(f"\n  HARD CONSTRAINTS ({len(hcs)}):")
    for c in hcs:
        src = f"  ← \"{c['source_in_profile']}\"" if c.get("source_in_profile") else ""
        print(f"    [{c['id']}] {c['type']}{src}")

    print(f"\n  PERSONAL CONSTRAINTS ({len(pcs)}):")
    for c in pcs:
        hop  = f"hop-{c.get('hop','?')}" if c.get("hop") else ""
        tier = c.get("score_tier","P")
        src  = f"\"{c.get('source_in_profile','?')}\"" if c.get("source_in_profile") else ""
        print(f"    [{c['id']}] {c.get('pattern','?'):30s} {hop:6s} [{tier}]")
        if c.get("description"):
            print(f"         {c['description'][:65]}")
        if src:
            print(f"         source: {src[:65]}")

    if bcs:
        print(f"\n  B-SCORE CONSTRAINTS ({len(bcs)}):")
        for c in bcs:
            print(f"    [{c['id']}] {c.get('pattern','?')} — {c.get('description','')[:55]}")

    # Check if required_venue_ids are in pool
    req_vids = rubric.get("required_venue_ids", [])
    if req_vids:
        print(f"\n  REQUIRED VENUES ({len(req_vids)}):")
        for vid in req_vids:
            v = pool_map.get(vid)
            name = v["name"] if v else "NOT IN POOL ⚠"
            print(f"    {vid}: {name}")


def print_validation(task: dict, pool: list[dict],
                     solvable: bool, reason: str,
                     schema_issues: list[str]):
    """Print validation results for a task."""
    print(f"\n  VALIDATION:")

    # Solvability
    if solvable:
        print(f"    Solvability:  ✅ ok")
    else:
        print(f"    Solvability:  ❌ {reason}")

    # Split hard issues from soft warnings (~-prefixed)
    hard = [i for i in schema_issues if not i.startswith("~ ")]
    soft = [i[2:] for i in schema_issues if i.startswith("~ ")]

    if hard:
        print(f"    Schema ({len(hard)} issue(s)):")
        for iss in hard:
            print(f"      ❌ {iss}")
    else:
        print(f"    Schema:       ✅ no hard issues")

    if soft:
        print(f"    Warnings ({len(soft)}):")
        for w in soft:
            print(f"      ~ {w}")



# ─────────────────────────────────────────────────────────────────────────────
# CHARACTER TRAIT DETECTION
# Cat 2 + Cat 5 + Cat 6 + Cat 7 = "character traits" — capped at ≤2 per task
# (Type 5 exception: up to 4)
# ─────────────────────────────────────────────────────────────────────────────

# Maps constraint pattern → character trait category (None = not a character trait)
_PATTERN_TO_TRAIT_CAT = {
    # Cat 2 — Physical/Dietary
    "regulation_required":        "cat2",   # wheelchair, photography, pet_friendly
    "label_required":             None,     # could be cat2 (vegetarian) or cat6 (art) — check params
    "label_excluded":             None,     # could be cat5 (no tourist) — check params
    "min_age":                    "cat2",
    # Cat 5 — Local Authenticity
    "hidden_gem_required":        "cat5",
    "local_cuisine_preference":   "cat5",
    # Cat 6 — Interests (only when label signals an interest, not a restriction)
    # Cat 7 — Occasion: detected via source_in_profile keywords
}

_CAT2_LABELS  = {"vegetarian","vegan","plant-based","halal","kosher","gluten-free",
                 "dog-friendly","family-friendly","children-friendly"}
_CAT5_LABELS  = {"tourist-trap","tourist","hidden-gem","locals-favourite","iconic"}
_CAT6_LABELS  = {"photography","art","contemporary-art","live-music","outdoor","museum",
                 "interactive","views","rooftop","garden","architecture"}
_CAT7_LABELS  = {"romantic","anniversary","celebration"}   # labels that indicate occasion
_CAT7_KEYWORDS = {"anniversary","honeymoon","birthday","romantic","celebrating",
                  "occasion","milestone","business","first time","returning"}

# Within-category clusters: signals in the same cluster are redundant/unrealistic to stack
# Rule: at most ONE cluster per category per task (type5 exempt)
_CAT2_CLUSTERS = {
    "dietary":   {"vegetarian","vegan","plant-based","halal","kosher","gluten-free"},
    "mobility":  {"wheelchair_accessible","wheelchair","limited mobility"},
    "allergy":   {"allergy","coeliac","intolerance","severe allergy"},
    "companion": {"dog-friendly","pet"},
}
_CAT5_CLUSTERS = {
    "seek_hidden":   {"hidden-gem","locals-favourite","tourist-trap","tourist"},
    "want_classics": {"iconic","classics"},
}


# ─────────────────────────────────────────────────────────────────────────────
# P13.1 — SCOPE DECLARATION SCHEMA CONSTANTS
# Used by _check_scope_declarations (currently dormant; wired in at P13.5).
# ─────────────────────────────────────────────────────────────────────────────
_VALID_SCOPE_MODES = frozenset({"universal", "scoped", "inclusion"})
_VALID_SCOPE_KEYS = frozenset({"activity_type", "category", "time_window"})
_VALID_CATEGORIES = frozenset({"restaurant", "cafe", "bar",
                                "museum", "attraction", "park", "neighbourhood"})
_VALID_ACTIVITY_TYPES = frozenset({"meal", "site", "any"})


def _check_scope_declarations(personal_constraints: list) -> list:
    """
    Validate scope_mode + scope fields on each personal_constraint per P13 design.

    Returns a list of human-readable issue strings. Empty list = all valid.

    Per P13 design: scope_mode is REQUIRED on all P-constraints; no silent
    defaults in code. Agents must declare explicitly.

    Valid shapes:
      scope_mode: "universal"   → scope field absent (or None) — applies plan-wide
      scope_mode: "scoped"      → scope: {activity_type?, category?, time_window?}
                                  with at least one of the three keys present
      scope_mode: "inclusion"   → params.min_count required (>= 1); scope ignored

    Note on combining scope keys: multiple keys (e.g. both activity_type AND
    category) are allowed — e.g. "dinner AT restaurants" legitimately uses both.

    This function is PURE — it never mutates constraints and has no side
    effects. Issue strings are prefixed with "[<pc_id>]" to match the
    convention used by validate_task_schema. Missing id → "[?]".

    Not called from anywhere in P13.1 — P13.5 wires it into validate_task_schema.
    """
    issues: list = []

    if not isinstance(personal_constraints, list):
        return issues

    for pc in personal_constraints:
        if not isinstance(pc, dict):
            # Malformed constraint — upstream schema checks will flag it;
            # we just don't crash here.
            continue

        pc_id = pc.get("id") if isinstance(pc.get("id"), str) and pc.get("id") else "?"
        prefix = f"[{pc_id}]"

        # scope_mode presence + value
        if "scope_mode" not in pc:
            issues.append(f"{prefix} scope_mode missing (required per P13; "
                          "choose one of universal, scoped, inclusion)")
            continue

        mode = pc.get("scope_mode")
        if not isinstance(mode, str) or mode not in _VALID_SCOPE_MODES:
            issues.append(
                f"{prefix} scope_mode must be one of "
                f"{sorted(_VALID_SCOPE_MODES)}, got {mode!r}"
            )
            continue

        scope = pc.get("scope")
        params = pc.get("params") if isinstance(pc.get("params"), dict) else {}

        if mode == "universal":
            if scope not in (None, {}):
                issues.append(
                    f"{prefix} scope_mode=universal should not have a scope "
                    f"field; got {scope!r}. Remove the scope field or set "
                    "scope_mode to 'scoped'."
                )
            # universal is otherwise always OK at this layer
            continue

        if mode == "scoped":
            if scope is None:
                issues.append(
                    f"{prefix} scope_mode=scoped requires a scope field with "
                    "at least one of activity_type, category, or time_window."
                )
                continue
            if not isinstance(scope, dict):
                issues.append(
                    f"{prefix} scope must be a dict, got "
                    f"{type(scope).__name__}: {scope!r}"
                )
                continue
            if not scope:
                issues.append(
                    f"{prefix} scope_mode=scoped requires scope to contain at "
                    "least one of activity_type, category, or time_window "
                    "(got empty dict)."
                )
                continue
            unknown_keys = set(scope.keys()) - _VALID_SCOPE_KEYS
            if unknown_keys:
                issues.append(
                    f"{prefix} scope has unknown key(s) {sorted(unknown_keys)}. "
                    f"Valid keys: {sorted(_VALID_SCOPE_KEYS)}."
                )
                # continue checking the valid keys too — emit more issues

            # Validate activity_type
            if "activity_type" in scope:
                at = scope["activity_type"]
                if not isinstance(at, str):
                    issues.append(
                        f"{prefix} scope.activity_type must be a string, got "
                        f"{type(at).__name__}"
                    )
                else:
                    at_stripped = at.strip()
                    if at_stripped not in _VALID_ACTIVITY_TYPES:
                        issues.append(
                            f"{prefix} scope.activity_type={at!r} not valid; "
                            f"allowed: {sorted(_VALID_ACTIVITY_TYPES)}"
                        )

            # Validate category (may be comma-separated list)
            if "category" in scope:
                cat_raw = scope["category"]
                if not isinstance(cat_raw, str):
                    issues.append(
                        f"{prefix} scope.category must be a string "
                        "(comma-separated for multiple), got "
                        f"{type(cat_raw).__name__}"
                    )
                else:
                    cats = [c.strip() for c in cat_raw.split(",") if c.strip()]
                    if not cats:
                        issues.append(
                            f"{prefix} scope.category is empty after parsing: {cat_raw!r}"
                        )
                    else:
                        invalid = [c for c in cats if c not in _VALID_CATEGORIES]
                        if invalid:
                            issues.append(
                                f"{prefix} scope.category has unknown value(s) "
                                f"{invalid}. Valid: {sorted(_VALID_CATEGORIES)}."
                            )

            # time_window: no strict format check yet — document-only for P13.1
            if "time_window" in scope:
                tw = scope["time_window"]
                if not isinstance(tw, str) or not tw.strip():
                    issues.append(
                        f"{prefix} scope.time_window must be a non-empty "
                        f"string, got {tw!r}"
                    )
            continue

        if mode == "inclusion":
            # min_count required and >= 1
            if "min_count" not in params:
                issues.append(
                    f"{prefix} scope_mode=inclusion requires params.min_count"
                )
                continue
            mc = params["min_count"]
            if not isinstance(mc, int) or isinstance(mc, bool):
                issues.append(
                    f"{prefix} params.min_count must be an int, got "
                    f"{type(mc).__name__}: {mc!r}"
                )
                continue
            if mc < 1:
                issues.append(
                    f"{prefix} params.min_count must be >= 1 for inclusion "
                    f"mode, got {mc}"
                )
                continue
            # scope field is ignored for inclusion (pool_filter in params is
            # the scope for inclusion), no check needed on scope here.
            continue

    return issues


def _constraint_trait_category(c: dict) -> str | None:
    """
    Return the character trait category (cat2/cat5/cat6/cat7) for a constraint,
    or None if it's not a character trait (cat1/cat3/cat4/cat8 signal).
    """
    pattern = c.get("pattern","")
    params  = c.get("params", {})
    source  = (c.get("source_in_profile","") or "").lower()

    # Explicit cat2 patterns
    if pattern in ("min_age", "noise_level_max"):
        return "cat2"
    if pattern == "regulation_required":
        rk = params.get("regulation_key","")
        if rk in ("wheelchair_accessible","pet_friendly","age_restriction"):
            return "cat2"
        if rk == "photography_allowed":
            return "cat6"   # photography is an interest
        return "cat2"       # default other regulations to cat2

    # cat7 via source keywords — check first, applies to all patterns
    if any(kw in source for kw in _CAT7_KEYWORDS):
        return "cat7"

    # label_required — depends on which label
    if pattern == "label_required":
        label = params.get("required_label","").lower()
        if label in _CAT2_LABELS:  return "cat2"
        if label in _CAT5_LABELS:  return "cat5"
        if label in _CAT6_LABELS:  return "cat6"
        if label in _CAT7_LABELS:  return "cat7"
        return None  # logistics/operational label

    # label_excluded
    if pattern == "label_excluded":
        label = params.get("excluded_label","").lower()
        if label in _CAT5_LABELS:  return "cat5"
        if label in _CAT2_LABELS:  return "cat2"
        if label in _CAT7_LABELS:  return "cat7"
        return None

    # cat5 patterns
    if pattern in ("hidden_gem_required","local_cuisine_preference","cuisine_diversity_minimum"):
        return "cat5"

    # TODO: After engine migration, most constraints use generic schema (scope/condition/
    # aggregation) without a pattern field. These pattern-based checks above only match
    # old-format or Bucket C constraints. This function needs to be extended to inspect
    # generic-schema conditions (e.g. has_tag:"vegetarian" → cat5, field:wheelchair → cat2)
    # for trait tracking to work on new tasks.

    # Everything else: cat1/cat3/cat4/cat8 — not a character trait
    return None


def _get_constraint_cluster(c: dict) -> tuple[str, str] | None:
    """
    Return (category, cluster_name) if this constraint belongs to a within-category
    cluster, else None. Used to detect unrealistic co-occurrence within one task.
    E.g. vegetarian + gluten-free = both ("cat2", "dietary") → flag.
    """
    pattern = c.get("pattern","")
    params  = c.get("params", {})
    if not isinstance(params, dict):
        # Malformed LLM output — params should always be a dict. Skip gracefully.
        params = {}
    source  = (c.get("source_in_profile","") or "").lower()

    # Cat 2 clusters
    label = str(params.get("required_label","") or "").lower()
    rk    = str(params.get("regulation_key","") or "")

    if label in _CAT2_CLUSTERS["dietary"] or any(k in source for k in _CAT2_CLUSTERS["dietary"]):
        return ("cat2", "dietary")
    if rk in _CAT2_CLUSTERS["mobility"] or any(k in source for k in _CAT2_CLUSTERS["mobility"]):
        return ("cat2", "mobility")
    if pattern == "regulation_required" and rk == "wheelchair_accessible":
        return ("cat2", "mobility")
    if label in _CAT2_CLUSTERS["allergy"] or any(k in source for k in _CAT2_CLUSTERS["allergy"]):
        return ("cat2", "allergy")
    if label in _CAT2_CLUSTERS["companion"] or any(k in source for k in _CAT2_CLUSTERS["companion"]):
        return ("cat2", "companion")

    # Cat 5 clusters
    if pattern == "hidden_gem_required" or label in _CAT5_CLUSTERS["seek_hidden"] or        any(k in source for k in {"hidden gem","tourist trap","locals favourite"}):
        return ("cat5", "seek_hidden")
    excl = params.get("excluded_label","").lower()
    if excl in _CAT5_CLUSTERS["seek_hidden"]:
        return ("cat5", "seek_hidden")
    if label in _CAT5_CLUSTERS["want_classics"] or        any(k in source for k in {"first time","classic","iconic"}):
        return ("cat5", "want_classics")

    return None


def _get_main_trait_key(c: dict) -> str | None:
    """
    Return a normalised trait key for overlap detection between tasks.
    E.g. vegetarian in task1 and vegan in task2 → both return "dietary_veggie".
    """
    pattern = c.get("pattern","")
    params  = c.get("params", {})
    if not isinstance(params, dict):
        params = {}
    source  = (c.get("source_in_profile","") or "").lower()

    label = str(params.get("required_label","") or "").lower()
    rk    = str(params.get("regulation_key","") or "")

    # Dietary
    if label in {"vegetarian","vegan","plant-based"} or "vegetarian" in source or "vegan" in source:
        return "dietary_veggie"
    if label in {"halal","kosher"} or "halal" in source or "kosher" in source:
        return "dietary_halal_kosher"
    if label == "gluten-free" or "gluten" in source:
        return "dietary_gluten"

    # Mobility
    if rk == "wheelchair_accessible" or "wheelchair" in source or "accessible" in source:
        return "mobility_wheelchair"
    if pattern == "min_age" or "age" in source:
        return "age_restriction"

    # Photography
    if rk == "photography_allowed" or "photo" in source or "camera" in source:
        return "interest_photography"

    # Local authenticity
    if pattern in ("hidden_gem_required",) or "hidden gem" in source or "tourist trap" in source:
        return "authenticity_hidden"
    if label == "iconic" or "iconic" in source or "classic" in source:
        return "authenticity_iconic"

    # Occasion
    if any(kw in source for kw in ("anniversary","honeymoon")):
        return "occasion_anniversary"
    if "birthday" in source:
        return "occasion_birthday"
    if "romantic" in source:
        return "occasion_romantic"
    if "business" in source:
        return "occasion_business"

    # Interest
    if "live music" in source or label == "live-music":
        return "interest_livemusic"
    if "art" in source or label in ("art","contemporary-art"):
        return "interest_art"
    if "outdoor" in source or label == "outdoor":
        return "interest_outdoor"

    return None

def validate_task_schema(task: dict, pool: list[dict] = None) -> list[str]:
    """
    Structural validation:
    - Catches type vs pattern, missing required fields, bad script_code
    - Enforces hop-2 rule (at least one hop-2 P-constraint per task)
    - Enforces constraint tension rule (at least one interacting pair)
    - A1: source_in_profile tracing check (placeholder detection + word overlap)
    - A2: python_script dry-run execution
    - A3: required_venue_ids pool membership
    - A5: pool-level constraint satisfiability via generic engine
    - Reports user_profile as a warning (collapsed to query-only schema)
    """
    import re as _re
    issues = []
    warnings = []

    for f in ["city","days","start_date","public_input","rubric"]:
        if f not in task:
            issues.append(f"Missing top-level field: '{f}'")

    pi = task.get("public_input", {})
    # Guard: some models JSON-encode public_input as a string — unwrap it
    if isinstance(pi, str):
        try:
            import json as _j; pi = _j.loads(pi)
        except Exception:
            pi = {}
    if not pi.get("query"):
        issues.append("public_input.query is empty")
    if pi.get("user_profile"):
        warnings.append("user_profile present — schema uses query-only; user_profile will be ignored by evaluator")
    if len(pi.get("query","")) < 20:
        issues.append("query is suspiciously short (< 20 chars)")
    if len(pi.get("query","")) > 600:
        warnings.append("query is long (> 600 chars) — real users write shorter messages")

    for kw in ["must_visit","label_required","regulation_required","hop:","score_tier","check_method","pattern:"]:
        if kw in pi.get("query","").lower():
            issues.append(f"query contains constraint jargon: '{kw}'")

    rubric = task.get("rubric", {})
    hcs = rubric.get("hard_constraints", [])
    pcs = rubric.get("personal_constraints", [])
    bcs = rubric.get("b_score_constraints", [])

    std_hc_ids = {c.get("type","") for c in hcs}
    for t in ("hours_check","no_overlap","travel_time_hard"):
        if t not in std_hc_ids:
            issues.append(f"Standard hard constraint '{t}' missing")

    if not pcs:
        issues.append("No personal constraints — task has no P-score signal")

    # ── A1: source_in_profile tracing ─────────────────────────────────────────
    _PLACEHOLDERS = {"n/a", "inferred", "implicit", "from context", "not stated",
                     "unstated", "assumed", "derived", "", "none", "na", "?"}
    _STOPWORDS    = {"a","an","the","and","or","in","on","at","to","of","is","it",
                     "my","me","we","our","i","for","with","that","this","be","by",
                     "as","was","are","from","so","but","if","up","do","not","have"}
    query_lower = pi.get("query", "").lower()

    for c in pcs:
        cid = c.get("id","?")
        is_generic = "scope" in c   # migrated to generic engine schema
        is_legacy  = "pattern" in c or "type" in c

        if "type" in c and "pattern" not in c and not is_generic:
            issues.append(f"[{cid}] used 'type' instead of 'pattern' — evaluator reads 'pattern'")
        elif not is_generic and not c.get("pattern"):
            issues.append(f"[{cid}] missing 'pattern' field")

        # Required fields differ by schema shape.
        if is_generic:
            # Generic schema: scope/condition/aggregation/consequence in place of pattern/params.
            for rf in ["id", "score_tier", "hop", "check_method", "source_in_profile",
                       "description", "scope", "condition", "aggregation"]:
                if rf not in c:
                    issues.append(f"[{cid}] missing required field '{rf}'")
        else:
            # Legacy pattern+params shape (Bucket C handlers).
            for rf in ["id","score_tier","hop","pattern","check_method","source_in_profile","description","params"]:
                if rf not in c:
                    issues.append(f"[{cid}] missing required field '{rf}'")
        # Bug 9 fix: None is not a valid score_tier — must be "P"
        if c.get("score_tier") != "P":
            st = c.get("score_tier")
            issues.append(f"[{cid}] score_tier must be 'P', got {st!r}")
        # Bug 10 fix: None is not a valid hop — must be 1 or 2
        if c.get("hop") not in (1, 2):
            issues.append(f"[{cid}] hop must be 1 or 2, got {c.get('hop')!r}")
        # Bug 11 fix: validate check_method value
        if c.get("check_method") not in ("code", "llm"):
            issues.append(f"[{cid}] check_method must be 'code' or 'llm', got {c.get('check_method')!r}")

        # A1 — source_in_profile check (hop-2 constraints only)
        if c.get("hop") == 2:
            sip = str(c.get("source_in_profile", "") or "").strip()
            if sip.lower() in _PLACEHOLDERS:
                issues.append(
                    f"[{cid}] source_in_profile is a placeholder ('{sip}') — "
                    f"hop-2 constraints must trace to a real signal in the query."
                )
            else:
                # Tier B soft warning: word overlap check
                content_words = [w for w in _re.sub(r"[^a-z ]", " ", sip.lower()).split()
                                  if w not in _STOPWORDS and len(w) > 2]
                if content_words:
                    matches = sum(1 for w in content_words if w in query_lower)
                    overlap = matches / len(content_words)
                    if overlap < 0.5:
                        warnings.append(
                            f"[{cid}] source_in_profile word overlap low "
                            f"({matches}/{len(content_words)} words found in query) — "
                            f"verify it traces to actual query language."
                        )

    # ── Character trait count rule ────────────────────────────────────────────
    # Normalise structural_type to the canonical full-string form. Agents
    # sometimes submit integers (1..6) or short forms ("type1"); we accept
    # these but collapse to the full "typeN_<name>" string so every
    # downstream check can do simple substring matching.
    _raw_st = task.get("structural_type", "")
    _CANONICAL_TYPES = {
        1: "type1_cascading_requirements",
        2: "type2_subset_selection",
        3: "type3_competing_requirements",
        4: "type4_precision_allocation",
        5: "type5_hard_feasibility_reduction",
        6: "type6_context_window_tension",
    }
    if isinstance(_raw_st, int) and _raw_st in _CANONICAL_TYPES:
        structural_type = _CANONICAL_TYPES[_raw_st]
        task["structural_type"] = structural_type  # ← write back so downstream sees canonical
        warnings.append(
            f"structural_type submitted as integer {_raw_st}; normalised to "
            f"'{structural_type}'. Next time, submit the full canonical string."
        )
    elif isinstance(_raw_st, str):
        _s = _raw_st.strip().lower()
        # Bare "type1".."type6" → expand to canonical
        m = _re.match(r"^type([1-6])$", _s)
        if m:
            structural_type = _CANONICAL_TYPES[int(m.group(1))]
            task["structural_type"] = structural_type
            warnings.append(
                f"structural_type submitted as '{_raw_st}'; normalised to "
                f"'{structural_type}'. Next time, submit the full canonical string."
            )
        # Bare digit as string
        elif _s in {"1","2","3","4","5","6"}:
            structural_type = _CANONICAL_TYPES[int(_s)]
            task["structural_type"] = structural_type
            warnings.append(
                f"structural_type submitted as '{_raw_st}'; normalised to "
                f"'{structural_type}'. Next time, submit the full canonical string."
            )
        else:
            structural_type = _raw_st
    else:
        structural_type = str(_raw_st) if _raw_st else ""

    is_type5          = "type5" in structural_type
    trait_cap         = 4 if is_type5 else 2

    trait_cats = [_constraint_trait_category(c) for c in pcs]
    trait_cats = [t for t in trait_cats if t is not None]
    n_traits   = len(set(trait_cats))

    if n_traits > trait_cap:
        issues.append(
            f"Too many character trait categories ({n_traits} found, max {trait_cap} "
            f"for {structural_type or 'this type'}). "
            f"Character traits = Cat 2 (dietary/physical) + Cat 5 (authenticity) + "
            f"Cat 6 (interests) + Cat 7 (occasion). "
            f"Cat 1 (composition), Cat 3 (schedule), Cat 4 (budget), Cat 8 (logistics) don't count. "
            f"Reduce to {trait_cap} distinct trait categories."
        )

    # ── Within-category cluster check ─────────────────────────────────────────
    if not is_type5:
        seen_clusters: dict[tuple, list[str]] = {}
        for c in pcs:
            cl = _get_constraint_cluster(c)
            if cl:
                seen_clusters.setdefault(cl, []).append(
                    c.get("params",{}).get("required_label") or
                    c.get("params",{}).get("regulation_key") or
                    c.get("pattern","?")
                )

        cat2_clusters = {k: v for k, v in seen_clusters.items() if k[0] == "cat2"}
        cat2_signals  = [sig for sigs in cat2_clusters.values() for sig in sigs]
        if len(cat2_signals) > 1:
            issues.append(
                f"Too many Cat 2 (physical/dietary) signals: {cat2_signals}. "
                f"Real travellers have at most one physical/dietary constraint per trip. "
                f"Pick the single most defining one (e.g. just 'vegetarian', not 'vegetarian + wheelchair'). "
                f"Type 5 tasks may stack multiple — use type5 if the stacking is intentional."
            )

        for (cat, cluster_name), signals in seen_clusters.items():
            if len(signals) > 1:
                issues.append(
                    f"Same-cluster stacking in {cat}/{cluster_name}: {signals}. "
                    f"These are the same signal expressed differently — pick one. "
                    f"(e.g. 'hidden gems' and 'no tourist traps' are identical constraints)"
                )

    # ── hop-2 rule ─────────────────────────────────────────────────────────────
    hop2_constraints = [c for c in pcs if c.get("hop") == 2]
    if pcs and not hop2_constraints:
        issues.append(
            "No hop-2 personal_constraint — every task needs at least one. "
            "hop-2 = the constraint requires reasoning about the traveller's situation, "
            "not just reading a label off the query. "
            "Examples: 'grandmother gets tired easily' → at_most 60min recommended_visit_minutes; "
            "'travelling with a baby' → regulation wheelchair_accessible=true; "
            "'on a tight budget' → price_tier constraint derived from spending context."
        )

    # ── V1: aggregation diversity ─────────────────────────────────────────────
    if len(pcs) >= 3:
        _agg_types = []
        for c in pcs:
            _a = c.get("aggregation")
            if isinstance(_a, dict):
                _agg_types.append(list(_a.keys())[0] if _a else "empty")
            else:
                _agg_types.append(str(_a))
        _n_at_least = _agg_types.count("at_least")
        if _n_at_least == len(pcs):
            issues.append(
                "All P-constraints use {at_least} aggregation — too uniform. "
                "At least one must use a different aggregation type: "
                "all, none, ratio, count_distinct, sum, at_most, at_most_distinct, at_least_days. "
                "See the AGGREGATION section in '-h validate' for examples."
            )

    # ── Bug fix: reject has_tag= as a scope value ─────────────────────────────
    # has_tag= is a condition dimension, not a structural scope path.
    # Valid scopes: all, per_day, activity_type=*, category=*, venue_id=*,
    # time_window=*, or a compound [list] of the above.
    # Putting has_tag= in scope instead of condition is a schema error that
    # produces vacuously-passing or silently-failing constraints.
    for _c in pcs:
        _s = _c.get("scope", "")
        if isinstance(_s, str) and _s.startswith("has_tag="):
            issues.append(
                f"[{_c.get('id','?')}] Invalid scope '{_s}': has_tag= is a "
                f"condition dimension, not a scope. Move it to the condition field: "
                f"condition={{\"has_tag\": \"{_s.split('=',1)[1]}\"}} and set "
                f"scope to a structural path (all, category=*, activity_type=*, "
                f"per_day, time_window=*, venue_id=*, or a compound list)."
            )

    # ── P6-T20 Fix #8: soft warning for off-universal has_tag/not_tag values ──
    # Catches the audit's failure mode where task prompts referenced removed-
    # vocab tags (outdoor, family-friendly, etc.) that no venue has after the
    # T2-B vocab cleanup. Soft warning rather than hard reject — cities may
    # have extension vocab we don't see at validator time.
    try:
        from scripts.generation.handbook import (
            UNIVERSAL_CORE_TAGS, UNIVERSAL_CUISINES,
        )
        _universal_pool = UNIVERSAL_CORE_TAGS | UNIVERSAL_CUISINES
    except Exception:
        _universal_pool = None

    def _extract_has_tag(cond):
        """Return all has_tag/not_tag values in a condition (handles lists)."""
        out = []
        if isinstance(cond, dict):
            for k in ("has_tag", "not_tag"):
                v = cond.get(k)
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, list):
                    out.extend([x for x in v if isinstance(x, str)])
        elif isinstance(cond, list):
            for sub in cond:
                out.extend(_extract_has_tag(sub))
        return out

    if _universal_pool is not None:
        for _c in pcs + hcs:
            cid = _c.get("id", _c.get("type", "?"))
            for tag in _extract_has_tag(_c.get("condition", {})):
                if tag not in _universal_pool:
                    warnings.append(
                        f"[{cid}] condition has_tag='{tag}' is not in the "
                        f"universal tag vocabulary. If this isn't a city "
                        f"extension, this constraint will match zero venues. "
                        f"Pick a tag from the universal vocab "
                        f"(e.g. {', '.join(sorted(_universal_pool)[:6])}, ...) "
                        f"or use the structured field/regulation equivalent."
                    )

    # ── V5: scope diversity ───────────────────────────────────────────────────
    _trivial_scopes = {"all", "activity_type=meal"}
    _scope_strs = []
    for c in pcs:
        _s = c.get("scope", "")
        if isinstance(_s, list):
            _scope_strs.append("[list]")
        else:
            _scope_strs.append(str(_s))
    if len(pcs) >= 3 and all(s in _trivial_scopes for s in _scope_strs):
        issues.append(
            "All P-constraints use scope 'all' or 'activity_type=meal' — too uniform. "
            "At least one must use a more specific scope: per_day, time_window=*, "
            "category=*, venue_id=*, has_tag=*, or a compound [list] scope."
        )


    # ── Empty-condition gate ───────────────────────────────────────────────────
    # A generic-schema constraint with condition={} AND a trivial scope ("all"
    # or "activity_type=X") has no filter power — it matches every venue.
    # Allowed only when scope is already specific enough to carry the filter
    # (e.g. scope="category=museum" with condition={} is legitimate — scope IS
    # the filter). Reject otherwise.
    for c in pcs:
        if "scope" not in c:
            continue   # Bucket C pattern+params — not checked here
        cid  = c.get("id", "?")
        cond = c.get("condition", {})
        sc   = c.get("scope", "all")
        agg  = c.get("aggregation", "all")
        if cond == {} or cond is None:
            # Empty condition is fine if scope is genuinely narrow
            sc_str = sc if isinstance(sc, str) else str(sc)
            scope_is_trivial = (
                sc == "all" or
                (isinstance(sc, str) and sc.startswith("activity_type="))
            )
            if scope_is_trivial:
                issues.append(
                    f"[{cid}] condition is empty with trivial scope={sc_str!r} — "
                    f"add has_tag, not_tag, or a field comparison to make the "
                    f"constraint meaningful. (Empty condition with scope='category=X' "
                    f"or 'venue_id=X' is fine — the scope itself is the filter.)"
                )

    # ── Constraint tension (type 3 only — A×A small-overlap metric) ───────────
    # Type 3 difficulty comes from two constraints pulling at DIFFERENT venue
    # sets.  We detect this via the engine's venues_matching: compute the venue
    # set each A×A constraint admits, then check overlap between pairs.
    #
    # A×A constraints: aggregation=="all" or =="none" (they filter the venue pool).
    # B×B constraints: counting aggregations (at_least, ratio, etc.) — they
    # constrain the plan space, not the venue pool.  B×B tension detection is
    # deferred (no real tasks use Bucket C B×B pattern pairs yet; will be
    # revisited when needed).
    #
    # Tension exemptions (per P6 design):
    #   type1: always satisfiable — cascading compounds, not competes
    #   type2: difficulty = time ceiling in query, not P-P interaction
    #   type4: difficulty = budget ceiling in query, not P-P interaction
    #   type5: difficulty = stacking → verified via lower bars, not overlap
    #   type6: always satisfiable — tension is quality of adaptation

    is_type3 = "type3" in structural_type
    is_type6 = "type6" in structural_type
    is_type1 = "type1" in structural_type
    is_type2 = "type2" in structural_type
    is_type4 = "type4" in structural_type
    tension_required = is_type3  # only type3 needs explicit A×A tension

    # ── V7: Type 4 must include a sum aggregation P-constraint ────────────────
    if is_type4:
        has_sum_constraint = any(
            isinstance(c.get("aggregation"), dict) and "sum" in c.get("aggregation", {})
            for c in pcs
        )
        if not has_sum_constraint:
            issues.append(
                "Type 4 (precision allocation) must include at least one P-constraint "
                "with sum aggregation to make the budget pressure scoreable. Example: "
                '{"scope": "per_day", "condition": {}, '
                '"aggregation": {"sum": "estimated_cost_local", "operator": "<=", '
                '"value": <budget_per_day>}}. '
                "This ensures the solving agent's allocation quality is measured, not "
                "just whether it stayed under budget."
            )

    tension_found  = False
    tension_detail = []

    # Pre-check for type3: must have ≥2 at_least inclusion constraints.
    # A universal filter (agg="all", scope="all") creates a narrowed pool,
    # not competing demands — that is type1 structure, not type3.
    if tension_required:
        at_least_pcs = [
            c for c in pcs
            if isinstance(c.get("aggregation"), dict) and "at_least" in c["aggregation"]
        ]
        universal_filter_pcs = [
            c for c in pcs
            if c.get("aggregation") in ("all", "none")
            and c.get("scope", "all") in ("all", "activity_type=meal",
                                           "activity_type=visit", "activity_type=any")
        ]
        if len(at_least_pcs) < 2:
            issues.append(
                f"Type 3 requires at least 2 'at_least' inclusion constraints to create "
                f"competing demands. Found only {len(at_least_pcs)}: "
                f"{[c.get('id') for c in at_least_pcs]}. "
                f"Both sides of the tension must use aggregation={{at_least: N}}."
            )
        if universal_filter_pcs:
            ids = [c.get('id') for c in universal_filter_pcs]
            issues.append(
                f"Type 3 constraint(s) {ids} use agg='all'/'none' with universal scope — "
                f"this narrows the pool (type1 structure), not creates competing demands. "
                f"Replace with at_least inclusion constraints, or move to a type1 task."
            )

    if tension_required and pool:
        from scripts.generation.constraint_engine import venues_matching as _vm

        # Build venue sets for all A×A generic-schema constraints
        axa_sets: list[tuple] = []   # (constraint, frozenset of venue_ids)
        for c in pcs:
            if "scope" not in c:
                continue   # Bucket C — skip for A×A
            agg = c.get("aggregation", "all")
            if agg not in ("all", "none"):
                continue   # B×B counting constraint — skip for A×A
            sc   = c.get("scope", "all")
            cond = c.get("condition", {})
            try:
                matched = _vm(pool, sc, cond)
                vids    = frozenset(v["venue_id"] for v in matched)
                # For "none" (exclusion), tension set is the complement
                if agg == "none":
                    all_ids = frozenset(v["venue_id"] for v in pool)
                    vids = all_ids - vids
                axa_sets.append((c, vids))
            except Exception:
                continue

        MIN_SIDE   = 3      # each set must have ≥3 venues
        MAX_OVERLAP_ABS = 3 # absolute overlap must be ≤3, OR...
        MAX_OVERLAP_REL = 0.25  # ...≤25% of smaller set

        for idx_i, (ci, set_i) in enumerate(axa_sets):
            for ci2, set_j in axa_sets[idx_i + 1:]:
                if len(set_i) < MIN_SIDE or len(set_j) < MIN_SIDE:
                    continue
                overlap  = len(set_i & set_j)
                smaller  = min(len(set_i), len(set_j))
                if overlap <= MAX_OVERLAP_ABS or overlap / smaller <= MAX_OVERLAP_REL:
                    tension_found = True
                    tension_detail.append(
                        f"[{ci.get('id','?')}]+[{ci2.get('id','?')}]: "
                        f"sets {len(set_i)}/{len(set_j)}, overlap={overlap} "
                        f"({overlap/smaller:.0%} of smaller)"
                    )

        # Bucket C pattern-pair stub: if two Bucket C patterns appear together
        # in a type 3 task, accept tentatively (B×B logic handles real cases).
        # These old-format patterns are kept outside the B×B pipeline for now.
        bucket_c_pats = {
            "weather_aware", "opening_time_required",
            "dependency_chain", "consecutive_pairs",
        }
        buc_pcs = [c for c in pcs if c.get("pattern","") in bucket_c_pats]
        if len(buc_pcs) >= 2:
            tension_found = True
            tension_detail.append(
                f"Bucket C pair: {buc_pcs[0].get('pattern')}+"
                f"{buc_pcs[1].get('pattern')} (old-format — accepted tentatively)"
            )

        # B×B tension detection: alternative to A×A for type 3.
        # If A×A tension already found, skip (either suffices).
        # If no A×A tension yet, compute B×B joint narrowing factor.
        # Type 3 uses 10% threshold (competing balance — always satisfiable).
        # Type 5 uses 5% threshold (hard feasibility — narrow intersection).
        bxb_threshold = 0.10 if is_type3 else 0.05
        if not tension_found and pool:
            bxb_pcs = [c for c in pcs
                       if "scope" in c and isinstance(c.get("aggregation"), dict)]
            if len(bxb_pcs) >= 2:
                try:
                    from scripts.generation.constraint_engine_bxb import _compute_bxb_joint
                    f_bxb, vc_bxb = _compute_bxb_joint(
                        bxb_pcs, pool, days=task.get("days", 1)
                    )
                    if f_bxb <= bxb_threshold:
                        tension_found = True
                        tension_detail.append(
                            f"B×B: joint_prob={f_bxb:.1%} <={bxb_threshold:.0%} "
                            f"(~{vc_bxb:.0f} valid plans)"
                        )
                except Exception:
                    pass  # B×B estimation failure is non-fatal

    if tension_required and len(pcs) >= 2 and not tension_found:
        issues.append(
            "No constraint tension detected. Type 3 needs two INCLUSION constraints "
            "(agg={at_least:N}) that compete for limited schedule slots.\n"
            "  Use at_least = min(2 × days, matching_venue_count) for each side.\n"
            "  GOOD pairs (different venue sets, competing counts):\n"
            "    {at_least:4, traffic_tier=high} vs {at_least:4, traffic_tier=low}\n"
            "    {at_least:4, has_tag=outdoor-seating} vs {at_least:4, price_tier>=upscale}\n"
            "  If tension is close, add a third constraint aligned with the persona.\n"
            "  BAD: using agg='all' as pool filters — these narrow or empty the pool\n"
            "    instead of creating competing demands on schedule slots.\n"
            f"  B×B threshold: joint valid-plan fraction must be ≤{bxb_threshold:.0%}."
        )

    # ── Type 6 window-grounding check ──────────────────────────────────────────
    # Type 6 difficulty comes from the persona conflicting with the SEASONAL WINDOW.
    # A task labelled type6 must have structural grounding in the window via a
    # P-constraint that references a window-specific property (time_window scope
    # or venue_id of a window-affected venue).
    # A type6 task with only generic pool constraints is just a type1 mislabelled.
    if is_type6:
        has_time_window_scope = any(
            "time_window" in str(c.get("scope", ""))
            for c in pcs if "scope" in c
        )
        has_venue_id_scope = any(
            isinstance(c.get("scope",""), str) and c.get("scope","").startswith("venue_id=")
            for c in pcs if "scope" in c
        )
        window_grounded = has_time_window_scope or has_venue_id_scope
        if not window_grounded:
            issues.append(
                "Type 6 task is not grounded in the seasonal window. "
                "Type 6 difficulty requires the persona to conflict with the window — "
                "this must appear structurally as: "
                "(a) a P-constraint using time_window= scope (schedule timing matters), OR "
                "(b) a P-constraint using venue_id= scope (specific window-affected venue). "
                "Without one of these, the task is a generic constraint task — "
                "reclassify as type1 or add a window-specific element."
            )


    # When a universal constraint uses has_tag/not_tag on a food-specific tag,
    # it silently filters out all SITE venues (which don't carry that tag).
    # Detect this by computing per-city tag affinity from the pool and warning
    # when a universal scope has a tag that's ≥80% concentrated in food categories.
    if pool:
        FOOD_CATS_SET = {"restaurant", "cafe", "bar"}
        SITE_CATS_SET = {"museum", "attraction", "park", "neighbourhood"}

        from collections import defaultdict as _dd
        _tag_cat: dict = _dd(lambda: _dd(int))
        _cat_count: dict = _dd(int)
        for v in pool:
            cat = v.get("category", "")
            _cat_count[cat] += 1
            for t in v.get("tags", []):
                _tag_cat[t][cat] += 1

        def _tag_affinity(tag: str):
            """Return 'food', 'site', or None (universal)."""
            cats = _tag_cat.get(tag, {})
            if not cats:
                return None
            total = sum(cats.values())
            food_frac = sum(cats.get(c, 0) for c in FOOD_CATS_SET) / total
            site_frac = sum(cats.get(c, 0) for c in SITE_CATS_SET) / total
            if food_frac >= 0.8:
                return "food"
            if site_frac >= 0.8:
                return "site"
            return None

        for c in pcs:
            if "scope" not in c:
                continue
            agg   = c.get("aggregation", "all")
            scope = c.get("scope", "all")
            cid   = c.get("id", "?")
            if agg != "all":
                continue   # only check universal constraints
            sc_str = scope if isinstance(scope, str) else str(scope)
            # Only flag when scope is "all" — scoped constraints are fine
            if sc_str != "all":
                continue

            def _extract_tags(cond):
                tags = []
                if isinstance(cond, dict):
                    if "has_tag" in cond:
                        tags.append(cond["has_tag"])
                    if "not_tag" in cond:
                        tags.append(cond["not_tag"])
                    for sub in cond.get("all", []) + cond.get("any", []):
                        tags.extend(_extract_tags(sub))
                return tags

            cond = c.get("condition", {})
            for tag in _extract_tags(cond):
                affinity = _tag_affinity(tag)
                if affinity == "food":
                    # Hard error: scope="all" + agg="all" + food-specific tag
                    # eliminates ALL site venues from the pool. This always
                    # fails the viable-schedule lower bar and burns agent turns.
                    issues.append(
                        f"[{cid}] tag '{tag}' only appears on food venues in this pool "
                        f"(≥80% restaurant/cafe/bar). Using it with scope='all' + "
                        f"aggregation='all' eliminates all museums, attractions, parks. "
                        f"Fix: change scope to 'activity_type=meal' so the constraint "
                        f"only applies to meal activities. The tag '{tag}' is fine — "
                        f"just scope it correctly."
                    )
                elif affinity == "site":
                    issues.append(
                        f"[{cid}] tag '{tag}' only appears on site venues in this pool "
                        f"(≥80% museum/attraction/park/neighbourhood). Using it with "
                        f"scope='all' + aggregation='all' eliminates all food venues. "
                        f"Fix: change scope to 'category=museum' (or the relevant "
                        f"category) so it only applies to site activities."
                    )

    # ── B-score: should be empty (generation removed, evaluator handles existing tasks) ──
    if bcs:
        issues.append(
            f"b_score_constraints should be empty (B-score generation is disabled). "
            f"Got {len(bcs)} constraint(s). Remove them or leave b_score_constraints as []."
        )

    # ── A3: required_venue_ids pool membership ─────────────────────────────────
    req_vids = rubric.get("required_venue_ids", [])
    if req_vids and pool:
        pool_ids = {v["venue_id"] for v in pool}
        missing_vids = [vid for vid in req_vids if vid not in pool_ids]
        if missing_vids:
            issues.append(
                f"required_venue_ids not in pool: {missing_vids}. "
                f"Only use venue_ids that exist in this city's pool."
            )

    # ── A5: pool-level constraint satisfiability ───────────────────────────────
    # Check that each generic-schema constraint has at least one matching venue.
    # Only checks constraints with non-empty conditions — empty conditions match
    # every venue by definition and don't need a pool check.
    if pool:
        try:
            from scripts.generation.constraint_engine import check_constraint_pool_satisfiability
            for c in pcs:
                if "scope" in c and c.get("consequence") in ("p_score_full", "f_score_hard", None):
                    # Skip constraints with empty/null conditions — they match every venue
                    cond = c.get("condition")
                    if not cond:
                        continue
                    ok, reason = check_constraint_pool_satisfiability(c, pool)
                    if not ok:
                        issues.append(
                            f"[{c.get('id','?')}] Pool satisfiability: {reason}"
                        )
        except ImportError:
            pass

    if not task.get("structural_type"):
        issues.append("Missing structural_type")
    try:
        _date.fromisoformat(task.get("start_date",""))
    except ValueError:
        issues.append(f"Invalid start_date: {task.get('start_date')}")

    # ── query_resources schema check (P7) ─────────────────────────────────────
    # Type 2 requires time_ceiling_minutes; type 4 requires budget_per_day.
    # Both live in public_input.query_resources and express the user-stated
    # resource ceiling in the query's natural language. Without them the
    # validator can't check whether the cheapest viable plan fits the ceiling.
    qr = pi.get("query_resources", {}) if isinstance(pi.get("query_resources"), dict) else {}
    if "type2" in structural_type:
        tcm = qr.get("time_ceiling_minutes")
        if tcm is None:
            issues.append(
                "Type 2 task missing public_input.query_resources.time_ceiling_minutes. "
                "State the numeric time ceiling the user's query implies (e.g. 'only 5.5 hours' → 330)."
            )
        elif not isinstance(tcm, (int, float)) or tcm <= 0:
            issues.append(
                f"public_input.query_resources.time_ceiling_minutes must be a positive number, "
                f"got {tcm!r}."
            )

        # P6-T4b: ceiling_mode / start_time / ceiling_scope validation
        cm = qr.get("ceiling_mode", "contiguous")    # default contiguous
        cs = qr.get("ceiling_scope", "total")        # default total
        st = qr.get("start_time")
        _VALID_CMODES  = {"contiguous", "spread"}
        _VALID_CSCOPES = {"total", "per_day"}
        if cm not in _VALID_CMODES:
            issues.append(
                f"public_input.query_resources.ceiling_mode must be one of "
                f"{sorted(_VALID_CMODES)} (default 'contiguous'), got {cm!r}."
            )
        if cs not in _VALID_CSCOPES:
            issues.append(
                f"public_input.query_resources.ceiling_scope must be one of "
                f"{sorted(_VALID_CSCOPES)} (default 'total'), got {cs!r}."
            )
        # contiguous mode requires start_time so the evaluator can build the
        # window [start_time, start_time + ceiling].
        if cm == "contiguous":
            if st is None:
                issues.append(
                    "Type 2 contiguous-mode task missing "
                    "public_input.query_resources.start_time. "
                    "State the HH:MM clock-time when the activity block begins "
                    "(e.g. '14:00' for 'Saturday afternoon from 2pm')."
                )
            elif not (isinstance(st, str) and _re.match(r"^\d{2}:\d{2}$", st)):
                issues.append(
                    f"public_input.query_resources.start_time must be 'HH:MM' "
                    f"format, got {st!r}."
                )
        # spread mode: start_time should NOT be set (it's meaningless for a
        # non-contiguous budget). Surface as a soft warning rather than reject.
        if cm == "spread" and st is not None:
            warnings.append(
                f"ceiling_mode=spread does not use start_time (got {st!r}); "
                "the budget is total time across the trip, not a fixed window. "
                "Remove start_time or switch to ceiling_mode=contiguous."
            )
        # ceiling_scope=per_day only makes sense for multi-day tasks (or with
        # spread mode); for a single-day contiguous block the two collapse.
        if cs == "per_day" and task.get("days", 1) == 1:
            warnings.append(
                "ceiling_scope=per_day on a 1-day task is equivalent to 'total'; "
                "the field has no effect."
            )
    if "type4" in structural_type:
        bpd = qr.get("budget_per_day")
        if bpd is None:
            issues.append(
                "Type 4 task missing public_input.query_resources.budget_per_day. "
                "State the numeric per-day budget from the query (in the city's local currency, no conversion)."
            )
        elif not isinstance(bpd, (int, float)) or bpd <= 0:
            issues.append(
                f"public_input.query_resources.budget_per_day must be a positive number, "
                f"got {bpd!r}."
            )

    # ── P6-T6 soft warning: at_least + scope=per_day on multi-day type2 ──────
    # K-target for type2 geometry now multiplies at_least by days when scope is
    # per_day (N venues per day × days). Flag the case so authors know the
    # ceiling pressure is N×days, not N — almost always they meant a global
    # floor instead.
    if "type2" in structural_type and task.get("days", 1) > 1:
        for c in task.get("rubric", {}).get("personal_constraints", []):
            agg = c.get("aggregation", {})
            if (isinstance(agg, dict) and "at_least" in agg
                    and c.get("scope") == "per_day"):
                pc_id = c.get("id", "?")
                n_per_day = agg["at_least"]
                warnings.append(
                    f"[{pc_id}] at_least with scope=per_day on a multi-day type2 "
                    f"task multiplies the floor by days "
                    f"({n_per_day}×{task['days']} = {n_per_day * task['days']} total venues). "
                    f"If you meant a global floor, use scope='all' or scope='category=X' instead."
                )

    return issues + [f"~ {w}" for w in warnings]


# ─────────────────────────────────────────────────────────────────────────────
# MODEL REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

ALL_MODELS = [
    "gemini-3.1-pro-preview",
    "claude-sonnet-4-5",
    "gpt-5.4",
    "deepseek-reasoner",
]


def _env_var_for_model(model: str) -> str:
    m = model.lower()
    if "deepseek" in m:
        return "DEEPSEEK_API_KEY"
    if m.startswith("gpt"):
        return "OPENAI_API_KEY"
    if "gemini" in m:
        return "GENAI_API_KEY"
    return "ANTHROPIC_API_KEY"


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# PER-WINDOW MODEL RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def _run_models_for_window(
    models_to_run, args,
    pool, cfg, window, event_briefs, unavailable, centre_lat,
    interests, registry, reg_dir,
    window_out_base,
    used_counts, quotas, free_slots, interest_counts,
    n_total_tasks, cat1_arg,
    type_keys_override=None,
) -> dict:
    """
    Run all models for one window. Returns {model: bool|None}.
    Mutates used_counts, registry in-place.

    When args.parallel_models is True (the default), models run concurrently
    via ThreadPoolExecutor; output is buffered per-model and printed in the
    order models complete. Mutation of shared state (used_counts, registry)
    is serialised via a per-window threading.Lock so composition quotas and
    trait non-overlap remain consistent. Each model hits a different provider,
    so rate limits don't contend.
    """
    import math as _math
    import io as _io
    import threading as _threading
    import concurrent.futures as _cf

    model_results: dict = {}
    parallel      = getattr(args, "parallel_models", True) and len(models_to_run) > 1
    state_lock    = _threading.Lock()

    # ── Thread-safe stdout proxy ──────────────────────────────────────────
    # contextlib.redirect_stdout is NOT thread-safe — it monkey-patches
    # sys.stdout globally, so parallel threads corrupt each other's prints
    # and can leave sys.stdout in a broken state after the ThreadPoolExecutor
    # exits. Instead, we install a per-thread stdout proxy ONCE for the
    # duration of the parallel phase. Each thread's prints go to its own
    # StringIO via threading.local; threads that aren't registered pass
    # through to the real stdout.
    _thread_buffers = _threading.local()
    _real_stdout    = sys.stdout

    class _ThreadLocalStdout:
        def write(self, s):
            buf = getattr(_thread_buffers, "buf", None)
            if buf is not None:
                buf.write(s)
            else:
                _real_stdout.write(s)
        def flush(self):
            buf = getattr(_thread_buffers, "buf", None)
            if buf is not None:
                try: buf.flush()
                except Exception: pass
            else:
                _real_stdout.flush()
        def isatty(self):
            return False
        # Defer any other attribute access to the real stdout so libraries
        # that poke at sys.stdout internals (e.g. checking encoding) don't
        # blow up.
        def __getattr__(self, name):
            return getattr(_real_stdout, name)

    _installed_proxy = False
    if parallel:
        sys.stdout = _ThreadLocalStdout()
        _installed_proxy = True

    def _run_one_model(model: str, capture: bool = True) -> tuple[str, bool | None, str]:
        """
        Generate + validate + save tasks for one model.
        Returns (model, result, captured_output).
        result: True | False | None (None = skipped for missing API key)

        When capture=True (parallel mode), per-thread prints are routed to an
        in-memory buffer via threading.local so threads don't interleave and
        sys.stdout isn't mutated globally. When capture=False (sequential),
        prints go straight to stdout so the user sees live progress.
        Shared-state mutations are always serialised via state_lock regardless.
        """
        buf = _io.StringIO() if capture else None
        if buf is not None:
            _thread_buffers.buf = buf  # register this thread's per-thread sink
        try:
            print(f"\n{chr(9608)*60}")
            print(f"MODEL: {model}")
            print(f"{chr(9608)*60}")

            # Resolve API key
            api_key = None
            if not args.dry_run:
                env_var = _env_var_for_model(model)
                api_key = os.environ.get(env_var)
                if not api_key:
                    print(f"  ⚠  Skipping — {env_var} not set")
                    return (model, None, buf.getvalue() if buf is not None else "")

            # Generate tasks
            if args.dry_run:
                print("  [dry-run — stub tasks, no API call]")
                n_stubs = len(args.types or ["type1","type2","type3","type4","type5","type6"]) \
                          if args.mode == "6type" else 2
                tasks = [_stub_task(args.city, window, pool, i) for i in range(n_stubs)]
                for i, t in enumerate(tasks):
                    t["city"]      = args.city
                    t["window_id"] = window["window_id"]
                    t["_is_stub"]  = True   # prevents saving in real-run output dirs
                    if args.mode == "6type":
                        type_keys = args.types or ["type1","type2","type3","type4","type5","type6"]
                        t["structural_type"] = _TYPE_SPECS[type_keys[i % len(type_keys)]]["name"]
            elif args.mode == "6type":
                try:
                    type_keys = type_keys_override or args.types or ["type1","type2","type3","type4","type5","type6"]
                    _agent_log_dir = window_out_base / "agent_logs"
                    # Pass a snapshot copy of used_counts so the agent's decisions
                    # are based on state at the start of its generation. The lock
                    # below serializes the post-generation updates.
                    with state_lock:
                        _used_snapshot = dict(used_counts)
                    tasks = generate_all_types(args.city, cfg, window, pool,
                                               api_key, model,
                                               registry=registry, type_keys=type_keys,
                                               interests=interests,
                                               cat1_override=cat1_arg,
                                               used_counts=_used_snapshot,
                                               quotas=quotas,
                                               n_tasks=n_total_tasks,
                                               event_briefs=event_briefs,
                                               venue_briefs=list(pool),
                                               unavailable=unavailable,
                                               centre_lat=centre_lat,
                                               max_turns=getattr(args, "max_turns", None),
                                               log_dir=_agent_log_dir,
                                               verbose=getattr(args, "verbose", False))
                    tasks = [t for t in tasks if "_error" not in t]
                except Exception as e:
                    print(f"\n  ❌ Generation error: {e}")
                    return (model, False, buf.getvalue())

            # Review each task
            all_ok = True
            for i, task in enumerate(tasks, 1):
                # Each task's post-agent processing is wrapped in a try/except
                # so a crash in one task (validator edge case, difficulty
                # annotator crash, disk write issue, etc.) doesn't kill the
                # whole model's thread in parallel mode. Tracebacks go to a
                # timestamped crash log in agent_logs for later inspection.
                _task_id_hint = task.get("task_id", f"task_{i}")
                _stype_hint   = task.get("structural_type", "?")
                try:
                    print_task_summary(task, pool, i)

                    schema_issues = validate_task_schema(task, pool)
                    solvable, reason = _verify_task_solvable(task, pool)

                    if not solvable:
                        all_ok = False
                    if any(not iss.startswith("~ ") for iss in schema_issues):
                        all_ok = False

                    print_validation(task, pool, solvable, reason, schema_issues)

                    if args.raw:
                        print(f"\n  RAW JSON:")
                        print(textwrap.indent(json.dumps(task, indent=2), "    "))

                    if True:
                        # Skip saving stub tasks unless this is an explicit --dry-run.
                        # In real runs, stub tasks indicate agent failure — the failure
                        # is already recorded in agent_logs/. Stubs in the task directory
                        # pollute validation analysis (they always fail schema checks).
                        if task.get("_is_stub") and not args.dry_run:
                            print(f"  ~ Stub task (agent failed) — not saved to output dir")
                            continue

                        model_slug = model.replace("/", "_").replace(":", "_")
                        out_dir = window_out_base / model_slug
                        out_dir.mkdir(parents=True, exist_ok=True)
                        tid = task.get("task_id", f"{args.city[:3]}_gen_{i:03d}")
                        out_path = out_dir / f"{tid}.json"

                        try:
                            from scripts.generation.compute_task_difficulty import annotate_task_difficulty
                            task = annotate_task_difficulty(task, args.city)
                            cc   = task.get("constraint_complexity", "?")
                            avd  = task.get("avg_venue_difficulty", "?")
                            psd  = task.get("pool_size_difficulty", "?")
                            psz  = task.get("filtered_pool_size", "?")
                            print(f"  Difficulty: cc={cc} avd={avd} psd={psd} "
                                  f"pool={psz} → {task.get('difficulty','?')}")
                        except Exception as _de:
                            print(f"  ~ Difficulty annotation failed: {_de}")

                        out_path.write_text(json.dumps(task, indent=2))
                        print(f"\n  Saved: {out_path}")

                        # ── Critical section: registry + used_counts mutations ──
                        with state_lock:
                            conflicts = register_task(
                                task, model, registry,
                                _get_main_trait_key, _get_constraint_cluster
                            )
                            if conflicts:
                                for conflict in conflicts:
                                    print(f"  ~ Registry: {conflict}")
                            save_registry(registry, reg_dir)

                            _comp_key = _detect_composition(task)
                            if _comp_key:
                                used_counts[_comp_key] = used_counts.get(_comp_key, 0) + 1
                                _used_str = f"{used_counts[_comp_key]}/{quotas.get(_comp_key,'?')}"
                                print(f"  ~ Composition: {_comp_key} [{_used_str}]")
                            else:
                                used_counts["__free__"] = used_counts.get("__free__", 0) + 1
                                print(f"  ~ Composition: (free slot)")

                except Exception as _post_exc:
                    import traceback as _tb
                    import time as _time
                    tb_str = _tb.format_exc()
                    print(f"\n  ❌ POST-AGENT CRASH for {_task_id_hint} "
                          f"({_stype_hint}): {type(_post_exc).__name__}: {_post_exc}")
                    print(f"     Task was generated successfully by the agent but "
                          f"crashed during validation/save. Continuing to next task.")
                    # Write a crash log so the traceback survives
                    try:
                        _crash_dir = window_out_base / "agent_logs"
                        _crash_dir.mkdir(parents=True, exist_ok=True)
                        _ts = int(_time.time())
                        _slug = model.replace("/", "_").replace(":", "_")
                        _win = window.get("window_id", "unknown")
                        _crash_path = (_crash_dir /
                            f"postagent_crash_{_slug}_{_win}_{_stype_hint}_{_ts}.json")
                        _crash_payload = {
                            "outcome":          "post_agent_crash",
                            "model":            model,
                            "window_id":        _win,
                            "task_id":          _task_id_hint,
                            "structural_type":  _stype_hint,
                            "exception_type":   type(_post_exc).__name__,
                            "exception_msg":    str(_post_exc),
                            "traceback":        tb_str,
                            "task":             task,  # preserve the agent's output
                        }
                        _crash_path.write_text(
                            json.dumps(_crash_payload, indent=2, default=str))
                        print(f"     📝 Crash log: {_crash_path}")
                    except Exception as _log_exc:
                        print(f"     (also failed to write crash log: {_log_exc})")
                    all_ok = False
                    # Continue the for-loop — don't let one task kill the model
                    continue

            # Cross-task trait overlap check
            if len(tasks) >= 2:
                readable = {
                    "dietary_veggie":       "vegetarian/vegan",
                    "dietary_halal_kosher": "halal/kosher",
                    "dietary_gluten":       "gluten-free",
                    "mobility_wheelchair":  "wheelchair",
                    "interest_photography": "photography",
                    "authenticity_hidden":  "hidden gems",
                    "authenticity_iconic":  "iconic sights",
                    "occasion_anniversary": "anniversary",
                    "occasion_birthday":    "birthday",
                    "occasion_romantic":    "romantic",
                    "interest_livemusic":   "live music",
                    "interest_art":         "art",
                    "interest_outdoor":     "outdoor",
                }
                task_trait_keys = []
                for t in tasks:
                    keys = set()
                    for c in t.get("rubric",{}).get("personal_constraints",[]):
                        k = _get_main_trait_key(c)
                        if k: keys.add(k)
                    task_trait_keys.append((t.get("task_id","?"), keys))

                overlap_found = False
                for i in range(len(task_trait_keys)):
                    for j in range(i+1, len(task_trait_keys)):
                        id_i, keys_i = task_trait_keys[i]
                        id_j, keys_j = task_trait_keys[j]
                        overlapping = keys_i & keys_j
                        if overlapping:
                            names = [readable.get(k,k) for k in overlapping]
                            print(f"\n  ⚠  CROSS-TASK TRAIT OVERLAP ({id_i} / {id_j}): "
                                  f"{', '.join(names)}")
                            overlap_found = True
                if overlap_found:
                    print(f"     Each main trait should appear in at most one task per run.")
                    all_ok = False

            return (model, all_ok, buf.getvalue() if buf is not None else "")
        except Exception as _fatal_exc:
            # Top-level catchall: any exception that escapes the agent loop,
            # post-agent processing, or other parts of _run_one_model would
            # otherwise be swallowed by ThreadPoolExecutor.as_completed and the
            # user would see "Model X crashed in parallel worker: Y" with no
            # traceback. Write a crash log to disk so the failure mode is
            # always visible, even if output capture lost everything.
            import traceback as _tb
            import time as _time
            tb_str = _tb.format_exc()
            try:
                print(f"\n  ❌ FATAL crash in _run_one_model({model}): "
                      f"{type(_fatal_exc).__name__}: {_fatal_exc}")
                print(f"     Writing crash log to disk.")
            except Exception:
                pass  # print itself may fail if stdout is broken
            try:
                _crash_dir = window_out_base / "agent_logs"
                _crash_dir.mkdir(parents=True, exist_ok=True)
                _ts = int(_time.time())
                _slug = model.replace("/", "_").replace(":", "_")
                _win = window.get("window_id", "unknown")
                _crash_path = (_crash_dir /
                    f"thread_crash_{_slug}_{_win}_{_ts}.json")
                _crash_payload = {
                    "outcome":        "thread_crash",
                    "model":          model,
                    "window_id":      _win,
                    "exception_type": type(_fatal_exc).__name__,
                    "exception_msg":  str(_fatal_exc),
                    "traceback":      tb_str,
                    "captured_output": buf.getvalue() if buf is not None else "",
                }
                _crash_path.write_text(
                    json.dumps(_crash_payload, indent=2, default=str))
            except Exception:
                pass  # don't let crash-logging failures hide the real crash
            # Always return a valid tuple so the executor doesn't raise out.
            return (model, False, buf.getvalue() if buf is not None else "")
        finally:
            # Clear this thread's buffer registration so the thread-local
            # doesn't leak across pool reuse.
            if buf is not None:
                try: del _thread_buffers.buf
                except AttributeError: pass

    # ── Execute: parallel or sequential ───────────────────────────────────
    try:
        if parallel:
            print(f"\n  [parallel mode: {len(models_to_run)} models via ThreadPoolExecutor]")
            with _cf.ThreadPoolExecutor(max_workers=len(models_to_run)) as pool_exec:
                futures = {pool_exec.submit(_run_one_model, m, True): m for m in models_to_run}
                for fut in _cf.as_completed(futures):
                    model = futures[fut]
                    try:
                        _model, result, output = fut.result()
                        print(output, end="")
                        model_results[_model] = result
                    except Exception as e:
                        import traceback as _tb
                        print(f"\n  ❌ Model {model} crashed in parallel worker: "
                              f"{type(e).__name__}: {e}")
                        print(_tb.format_exc())
                        model_results[model] = False
        else:
            # Sequential: output already went straight to stdout via capture=False,
            # so don't re-print the (empty) returned buffer.
            for model in models_to_run:
                try:
                    _model, result, _ = _run_one_model(model, capture=False)
                    model_results[_model] = result
                except Exception as e:
                    import traceback as _tb
                    print(f"\n  ❌ Model {model} crashed: {type(e).__name__}: {e}")
                    print(_tb.format_exc())
                    model_results[model] = False
    finally:
        # Always restore sys.stdout to the real thing when leaving this
        # function so subsequent window loops / summaries print to the terminal.
        if _installed_proxy:
            sys.stdout = _real_stdout

    return model_results


def main():
    parser = argparse.ArgumentParser(
        description="Generate and inspect 2 benchmark tasks across all models",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all models (default)
  python test_generate_tasks.py --city london

  # Single model only
  python test_generate_tasks.py --city london --model gpt-5.4

  # Stub only (no API calls)
  python test_generate_tasks.py --city london --dry-run

  # Save tasks to disk (per-model subdirectory)
  python test_generate_tasks.py --city london --output data/cities/london/tasks/
        """
    )
    parser.add_argument("--city",    default="london",
                        help="City to generate tasks for (must have a per-city DB)")
    parser.add_argument("--run-name", default=None,
                        help="Run name for separate DB (e.g. test_30). Default: production DB.")
    parser.add_argument("--window",  default=None,
                        help="Seasonal window ID (optional; uses default if not set)")
    parser.add_argument("--model",   default=None,
                        help="Run a single model instead of all models")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate stub tasks without API calls")
    parser.add_argument("--output",  type=Path,
                        default=None,
                        help="Override output base dir. Default: data/cities/{city}/tasks/[runs/{run-name}/]{window_id}/")
    parser.add_argument("--raw",     action="store_true",
                        help="Also print full raw task JSON")
    parser.add_argument("--verbose", action="store_true",
                        help="Print per-turn agent activity (tool calls + results) during "
                             "generation. Useful for seeing live progress instead of silent "
                             "waits on long LLM calls. Each turn prints tool name and "
                             "status as it completes.")
    parser.add_argument("--mode",    default="6type",
                        choices=["6type","2task"],
                        help="6type: one task per structural type (default). "
                             "2task: legacy 2-task free-choice mode.")
    parser.add_argument("--types",   default=None, nargs="+",
                        choices=["type1","type2","type3","type4","type5","type6"],
                        help="With --mode 6type: only generate these types")
    parser.add_argument("--cat1",    default=None,
                        choices=list(CAT1_OPTIONS.keys()),
                        help=(
                            "Override Cat 1 composition for ALL tasks in this run. "
                            "Options: " + ", ".join(CAT1_OPTIONS.keys()) + ". "
                            "Example: --cat1 friends_large  "
                            "Overrides the slot system for all types."
                        ))
    parser.add_argument("--single-shot", action="store_true",
                        help="Use legacy single-shot LLM call instead of agent loop. "
                             "Faster and cheaper — useful for comparison and debugging.")
    parser.add_argument("--max-turns", type=int, default=None,
                        help="Override agent loop turn limit (default: 15). "
                             "Example: --max-turns 20")
    parser.add_argument("--count", type=int, default=None,
                        help="Number of tasks to generate per model per window (1-6). "
                             "Selects the first N structural types. "
                             "If omitted, uses the full n_tasks logic.")
    parser.add_argument("--parallel-models", dest="parallel_models",
                        action="store_true", default=True,
                        help="Run models concurrently within each window (default). "
                             "Each model hits a different provider, so rate limits "
                             "don't contend. Expect ~5× wall-time reduction for "
                             "multi-model runs. Output is buffered per-model and "
                             "printed in completion order.")
    parser.add_argument("--no-parallel-models", dest="parallel_models",
                        action="store_false",
                        help="Disable parallel model execution — useful for debugging "
                             "a single model or step-by-step inspection of shared-state "
                             "mutations.")
    args = parser.parse_args()

    import os

    models_to_run = [args.model] if args.model else ALL_MODELS

    print(f"\n{'═'*60}")
    print(f"TRAVELBENCH — Task Generation Test Run")
    mode_label = "6-type distributed" if args.mode == "6type" else "2-task free-choice"
    print(f"City: {args.city.title()}  |  Mode: {mode_label}  |  Models: {len(models_to_run)}")
    print(f"{'═'*60}")

    # ── Load pool (shared across all windows and models) ─────────────────────
    print(f"\nLoading venue pool for '{args.city}'...")
    _run_name = getattr(args, 'run_name', None)
    from scripts.generation.db import get_city_db_path as _gcdbp
    _db_path = _gcdbp(args.city, run_name=_run_name)
    try:
        pool, cfg = load_pool(args.city, db_path=_db_path)
    except ValueError as _e:
        print(f"\nERROR: {_e}")
        if _run_name:
            print(f"Hint: ensure --run-name {_run_name} has a populated venue DB.")
        else:
            print("Hint: use --run-name to point at a populated DB, e.g. --run-name test_50")
        return
    print(f"  {len(pool)} verified venues loaded")

    import math as _math
    _hi = [(v["lat"], v["lng"]) for v in pool
           if v.get("traffic_tier") == "high" and v.get("lat") and v.get("lng")]
    if not _hi:
        _hi = [(v["lat"], v["lng"]) for v in pool if v.get("lat") and v.get("lng")]
    centre_lat = (sum(c[0] for c in _hi) / len(_hi)) if _hi else 48.8
    print(f"  City centre lat: {centre_lat:.3f} "
          f"(lng_factor={round(_math.cos(_math.radians(centre_lat))*111,1)})")

    interests = generate_city_interests(cfg, api_key=None)
    print(f"  City interests ({len(interests)}): {', '.join(interests[:5])}...")

    from scripts.generation.generate_task import get_task_output_dir
    _base_output = args.output if args.output else None
    _reg_dir = (_base_output / "registry") if _base_output else (
        ROOT / "data" / "data1" / "tasks" / "unfiltered"
    )
    registry = scan_existing_tasks(args.city, _reg_dir,
                                    get_trait_key_fn=_get_main_trait_key,
                                    get_cluster_fn=_get_constraint_cluster)
    print(f"  Trait registry: {len(registry.get('used_traits',{}))} trait(s) used")

    # Resolve windows to run
    from scripts.generation.populate_seasonal_windows import get_windows as _get_all_windows
    if args.window:
        windows_to_run = [get_window_for_city(args.city, args.window, db_path=_db_path)]
    else:
        windows_to_run = _get_all_windows(args.city, db_path=_db_path)
        if not windows_to_run:
            windows_to_run = [get_window_for_city(args.city, None, db_path=_db_path)]
        print(f"  Windows ({len(windows_to_run)}): "
              f"{', '.join(w['label'] for w in windows_to_run)}")

    # Cat 1 quotas — scaled across ALL windows × models
    _all_types    = getattr(args, "types", None) or ["type1","type2","type3","type4","type5","type6"]
    if getattr(args, "count", None):
        _all_types = _all_types[:args.count]
    n_types       = len(_all_types)
    n_total_tasks = len(models_to_run) * n_types * len(windows_to_run)
    cat1_arg      = getattr(args, "cat1", None)
    quotas        = compute_cat1_quotas(n_total_tasks)
    used_counts: dict[str, int] = {"__free__": 0}
    free_slots    = max(1, round(n_total_tasks * CAT1_FREE_PCT))
    interest_counts: dict[str, int] = {i: 0 for i in interests}

    print(f"\n  Cat 1 quotas (across {n_total_tasks} tasks, "
          f"{len(windows_to_run)} window(s), {len(models_to_run)} model(s)):")
    for k, q in quotas.items():
        print(f"    {k:15s}  max={q:2d}  ({CAT1_QUOTAS[k]:.0%})  {CAT1_OPTIONS[k]}")
    print(f"    {'(free)':15s}  max={free_slots:2d}  (5%)  no composition assigned")

    print(f"\nVENUE POOL SNAPSHOT (first 8):")
    for v in pool[:8]:
        wi = " [WI]" if v.get("has_wrong_info") else ""
        ff = " [FAM]" if v.get("family_friendly") else ""
        print(f"  {v['venue_id']:12s} {v['name'][:28]:28s} {v['category']:12s} "
              f"{v.get('traffic_tier','?'):4s} {wi}{ff}")
    if len(pool) > 8:
        print(f"  ... and {len(pool)-8} more")

    # ── Per-window loop ───────────────────────────────────────────────────────
    all_model_results: dict[str, bool] = {}

    for window in windows_to_run:
        _wid = window.get("window_id", "")

        print(f"\n{'▓'*60}")
        print(f"WINDOW: {window['label']}  "
              f"({window['dates'][0]} – {window['dates'][-1]})")
        print(f"{'▓'*60}")

        if _base_output:
            _window_out_base = _base_output / _wid
        else:
            _window_out_base = get_task_output_dir(args.city, _wid, run_name=_run_name)

        # Load event briefs for this window
        try:
            from scripts.generation.db import get_connection
            _conn = get_connection(_db_path)
            _evs = _conn.execute("""
                SELECT e.venue_id, v.name as venue_name, v.category,
                       e.name, e.description, e.start_date, e.end_date,
                       e.affects_hours, e.affects_access, e.sold_out,
                       e.modified_hours, e.price_local
                FROM events e
                JOIN venues v ON e.venue_id = v.venue_id
                WHERE e.city = ? AND e.window_id = ?
            """, (args.city, _wid)).fetchall()
            _conn.close()
            event_briefs = []
            _pidx = {v["venue_id"]: i for i, v in enumerate(pool)}
            for row in _evs:
                row_d = dict(row)
                vid = row_d.pop("venue_id")
                row_d["venue_index"] = _pidx.get(vid, -1)
                row_d["task_hook"]   = "general"
                event_briefs.append(row_d)
            print(f"  Events: {len(event_briefs)} loaded" if event_briefs
                  else f"  Events: none in DB for this window")
        except Exception as _e:
            event_briefs = []
            print(f"  Events: could not load ({_e})")

        # Load sold-out dates for this window
        try:
            from scripts.generation.generate_task import load_unavailable_dates
            unavailable = load_unavailable_dates(
                args.city, window.get("dates", []), db_path=_db_path
            )
            n_sold = sum(len(v) for v in unavailable.values())
            if n_sold:
                print(f"  Sold-out: {n_sold} entries across {len(unavailable)} venues")
        except Exception as _ue:
            unavailable = {}

        print(f"  Output → {_window_out_base}/{{model_slug}}/")

        window_results = _run_models_for_window(
            models_to_run=models_to_run,
            args=args,
            pool=pool, cfg=cfg,
            window=window, event_briefs=event_briefs,
            unavailable=unavailable, centre_lat=centre_lat,
            interests=interests, registry=registry, reg_dir=_reg_dir,
            window_out_base=_window_out_base,
            used_counts=used_counts, quotas=quotas,
            free_slots=free_slots, interest_counts=interest_counts,
            n_total_tasks=n_total_tasks, cat1_arg=cat1_arg,
            type_keys_override=_all_types,
        )
        for m, ok in window_results.items():
            prev = all_model_results.get(m)
            all_model_results[m] = False if ok is False else (ok if prev is None else prev)

    model_results = all_model_results

    # ── Final summary across all models ──────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"SUMMARY")
    print(f"{'═'*60}")
    for model, ok in model_results.items():
        if ok is None:
            status = "SKIPPED (no API key)"
        elif ok:
            status = "✅ passed"
        else:
            status = "❌ issues found"
        print(f"  {model:35s}  {status}")
    print(f"{'═'*60}")

    # ── Composition usage summary ─────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"CAT 1 COMPOSITION USAGE")
    print(f"{'═'*60}")
    any_over = False
    for k in list(CAT1_OPTIONS.keys()) + ["__free__"]:
        used  = used_counts.get(k, 0)
        if used == 0:
            continue
        if k == "__free__":
            label = "(free/unassigned)"
            quota = free_slots
        else:
            label = CAT1_OPTIONS[k]
            quota = quotas.get(k, 1)
        bar    = "█" * used
        over   = " ⚠ OVER QUOTA" if used > quota else ""
        if over:
            any_over = True
        print(f"  {k:15s}  {bar:10s} {used:2d}/{quota}  {label}{over}")

    not_used = [k for k in CAT1_OPTIONS if used_counts.get(k, 0) == 0]
    if not_used:
        print(f"\n  Unused: {', '.join(not_used)}")
    if any_over:
        print(f"\n  ⚠  Some compositions exceeded quota — reduce repeats in next run")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()