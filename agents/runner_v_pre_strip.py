"""
agents/runner.py

Runs a Claude agent on a TravelBench task using the 4 mock tools.
Records every tool call and the final parsed plan for evaluation.

Usage:
    python agents/runner.py --task par_easy_001 --api-key KEY
    python agents/runner.py --task par_medium_001 --model claude-opus-4-6 --api-key KEY
    python agents/runner.py --task par_easy_001 --dry-run   # no API, stub output
"""

import json, sys, re, argparse
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR  = ROOT / "data"
RESULTS_DIR = ROOT / "results"
RESULTS_DIR.mkdir(exist_ok=True)

try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

from server.mock_tools import TOOL_SCHEMAS, dispatch_tool

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(task: dict, city_config: dict = None) -> str:
    """Build a city-aware system prompt for the solving agent."""
    city = task.get("city", "the city")
    window_id = task.get("window_id", "")
    start_date = task.get("start_date", "")

    # Seasonal context
    _season_line = ""
    if city_config and city_config.get("seasonal_windows"):
        import json as _json
        try:
            windows = _json.loads(city_config["seasonal_windows"]) \
                      if isinstance(city_config["seasonal_windows"], str) \
                      else city_config["seasonal_windows"]
            for w in windows:
                if w.get("window_id") == window_id:
                    _season_line = (
                        f"\n\nSEASONAL CONTEXT: This trip falls during "
                        f"\"{w.get('label', window_id)}\". "
                        f"Some venues may have special hours, closures, or events "
                        f"during this period. Check official sites for date-specific info."
                    )
                    break
        except Exception:
            pass

    return f"""You are an expert travel planning assistant for {city}. Your task is to create a detailed, day-by-day travel itinerary using the available tools.

## Information Reliability — CRITICAL
**NOTHING provided to you is guaranteed to be ground truth.** Every source can be
wrong, outdated, or incomplete. Your job is to cross-check and find the truth.

### Source hierarchy (most to least reliable):
1. **get_official_site** — the ONLY authoritative source. Hours, ticket availability,
   regulations, and labels from official sites are ALWAYS correct. When official site
   data conflicts with any other source, the official site wins. ALWAYS check this
   for booking-required venues and any venue where you plan time-sensitive activities.

2. **search_blogs_and_forums** — user-generated content with dates, likes, and saves.
   Blog posts and forum threads often contain CORRECTIONS to outdated directory info.
   Pay attention to:
   - **Post dates**: recent posts (last 6 months) are more reliable
   - **Engagement**: high likes/saves = community-verified information
   - **Specific corrections**: "the hours on Yelp are wrong, they actually close at 6"
   If a blog post contradicts Yelp, the blog post is probably right.

3. **search_yelp** — venue directory. Use for DISCOVERY ONLY (finding venues, getting
   venue_ids, seeing what exists). Searches match against venue names, tags (e.g.
   "hidden-gem", "fine-dining", "craft-beer", "romantic"), and regulations (e.g.
   "wheelchair accessible", "pet friendly", "quiet"). Use the `category` parameter
   to filter by venue type (restaurant, cafe, museum, etc.).
   **DO NOT trust Yelp hours for scheduling.**
   Yelp hours are staff-registered and frequently outdated — venues change hours
   seasonally, for holidays, or permanently without updating their listing.
   Check `last_activity_date` — if it's old, the data is likely stale.

### Cross-checking workflow:
For EVERY venue you include in your final plan:
1. Discover it via search_yelp (get the venue_id)
2. Check blogs/forums for recent reports about hours, prices, closures
3. If the venue has an official site → call get_official_site with your travel date
4. If sources conflict → use the official site hours, NOT Yelp hours
5. If no official site and blog says different hours than Yelp → use the blog info

**Common trap**: Yelp shows a restaurant open 09:00-23:00 but it actually opens at
17:30 for dinner only. If you schedule lunch there based on Yelp, your plan fails.
The blog or official site would have told you the real hours.

### Travel time tool:
- **get_travel_time** — returns travel time between two venues for walking, transit, and
  cycling. Use transit for long distances (>30min walk) and walking for short ones.

## Scoring Requirements — READ CAREFULLY
Your plan will be scored to 0% (automatic fail) if ANY of these are missing:

1. You MUST call **search_yelp** at least once to discover venues
2. You MUST call **search_blogs_and_forums** at least once to cross-check info
3. You MUST call **get_official_site** for every venue in your plan that has one
4. You MUST call **get_travel_time** for consecutive venue pairs in your plan
5. You MUST wrap your final plan in **<final_plan>** tags with valid JSON

Skipping any tool means your plan is based on incomplete or potentially wrong
information — the benchmark treats this as a process failure.

## Tool Call Budget — PLAN CAREFULLY
Your total tool call cap is **15 × number of trip days**. For example:
- 1-day trip: 15 total calls
- 2-day trip: 30 total calls
- 3-day trip: 45 total calls

You should use MOST of your budget. A well-researched day needs 9-14 calls:
- 2-3 search_yelp (venue discovery — each returns multiple results)
- 1-2 search_blogs_and_forums (cross-check and find corrections)
- 2-3 get_official_site (booking-required and key venues)
- 3-4 get_travel_time (consecutive venue pairs)

If you're planning a 3-day trip with only 10-15 tool calls total, you are NOT doing
enough research — you need 30-40+ calls to properly verify venues, hours, and travel.

Tips:
- Use broad searches first (e.g. "restaurant" returns 6 results at once)
- Don't repeat failed searches with slight variations — try a different approach
- Check official sites for booking-required venues before finalizing your plan
- Get travel times after selecting venues, before writing the final plan

## Transport Activities
Include a transport activity between venue visits when you need to travel. This makes
your plan explicit about how you're getting around. Format:

  {{"time_start": "14:30", "time_end": "15:00", "activity_type": "transport",
    "mode": "transit", "from_venue_id": "abc", "to_venue_id": "def",
    "estimated_cost_local": 3}}

Modes: "walking", "transit", "cycling". The transport duration (time_end - time_start)
must be at least as long as the travel time returned by get_travel_time for that mode.
If the gap between two venue activities minus travel time is less than 30 minutes,
add a rest/leisure stop.

## Regulation Awareness
When a user mentions travelling with pets, needing wheelchair access, dietary restrictions,
dress code concerns, or any other special requirement — check every venue for compliance.
If a venue's policy cannot be confirmed from sources, add a flag in the activity's
`flags` field: e.g. "pet_policy_unconfirmed — please verify before visiting".
{_season_line}

## Output Format
After completing your research, output your final plan as a JSON object wrapped in
<final_plan> tags. The JSON must be valid.

<final_plan>
{{
  "task_id": "string",
  "city": "{city}",
  "days": [
    {{
      "day": 1,
      "day_of_week": "fri",
      "activities": [
        {{
          "time_start": "HH:MM",
          "time_end": "HH:MM",
          "venue_id": "exact_id_from_search_results",
          "venue_name": "string",
          "activity_type": "meal | visit | leisure | shopping | transport",
          "estimated_cost_local": 0,
          "flags": ["any regulation warnings or unconfirmed policies"]
        }}
      ]
    }}
  ],
  "total_estimated_cost_local": 0,
  "unresolved_flags": ["summary of anything you could not confirm"],
  "planning_notes": "brief summary of key decisions and sources used"
}}
</final_plan>

Rules:
- day_of_week must be one of: mon, tue, wed, thu, fri, sat, sun
- activity_type must be one of: meal, visit, leisure, shopping, transport
- time_start and time_end must be in HH:MM format (24-hour)
- venue_id must be the exact ID returned by search tools (for venue activities)
- transport activities use from_venue_id + to_venue_id + mode instead of venue_id
- leisure activities serve as rest/buffer stops and should have a venue_id
- estimated_cost_local is in local currency
- **time_end must never exceed the venue's closing time** — if a 2-hour visit
  starts at 16:30 but the venue closes at 18:00, set time_end to 18:00 not 18:30.
  Check every activity's time_end against the venue's verified close time before
  writing the final plan. Split-service venues (e.g. lunch 12:00-14:00, dinner
  19:00-21:30) close between services — do not schedule across the gap.
"""

# ─────────────────────────────────────────────────────────────────────────────
# PLAN PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_final_plan(text: str) -> dict | None:
    match = re.search(r'<final_plan>\s*(.*?)\s*</final_plan>', text, re.DOTALL)
    if not match:
        return None
    raw = match.group(1).strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r'^```[a-z]*\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw.strip())
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# AGENT RUNNER
# ─────────────────────────────────────────────────────────────────────────────

RESULT_SCHEMA_VERSION = "1.1"   # bump when result JSON shape changes


def run_agent(task: dict, model: str, api_key: str) -> dict:
    """Run the agent with full tool loop. Returns result dict."""
    client = anthropic.Anthropic(api_key=api_key)

    # Compute start_day_of_week from start_date if missing
    _dow = task.get("start_day_of_week")
    if not _dow and task.get("start_date"):
        from datetime import date as _date
        _d = _date.fromisoformat(task["start_date"])
        _dow = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][_d.weekday()]

    # Build user message — user_profile is optional (London tasks embed persona in query)
    _profile = task.get("public_input", {}).get("user_profile", "")
    _profile_line = f"\n\nAbout me: {_profile}" if _profile else ""

    user_message = (
        f"{task['public_input']['query']}"
        f"{_profile_line}\n\n"
        f"Trip details: {task['days']} day(s) starting {task['start_date']} "
        f"({_dow.capitalize() if _dow else 'unknown'})."
    )

    messages = [{"role": "user", "content": user_message}]
    tool_call_log = []
    round_count = 0
    final_text = ""

    # Load city config for system prompt context
    _city_config = None
    try:
        from scripts.generation.db import get_connection, get_city_db_path
        _city = task.get("city", "").lower()
        _db = get_city_db_path(_city)
        if _db.exists():
            _conn = get_connection(_db)
            _row = _conn.execute(
                "SELECT * FROM city_config WHERE city = ?", (_city,)
            ).fetchone()
            if _row:
                _city_config = dict(_row)
            _conn.close()
    except Exception:
        pass
    system_prompt = _build_system_prompt(task, _city_config)

    while round_count < MAX_TOOL_ROUNDS:
        round_count += 1

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOL_SCHEMAS,
            messages=messages
        )

        # Collect assistant message content
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # Check stop reason
        if response.stop_reason == "end_turn":
            # Extract final text
            for block in assistant_content:
                if hasattr(block, "text"):
                    final_text += block.text
            break

        if response.stop_reason != "tool_use":
            # Unexpected stop
            for block in assistant_content:
                if hasattr(block, "text"):
                    final_text += block.text
            break

        # Process tool calls
        tool_results = []
        for block in assistant_content:
            if block.type != "tool_use":
                continue

            tool_name  = block.name
            tool_input = block.input
            tool_id    = block.id

            # Dispatch to mock tools
            tool_output = dispatch_tool(tool_name, tool_input)

            tool_call_log.append({
                "round": round_count,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_output": tool_output,
            })

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_id,
                "content": json.dumps(tool_output)
            })

        messages.append({"role": "user", "content": tool_results})

    parsed_plan = parse_final_plan(final_text)

    return {
        "schema_version":   RESULT_SCHEMA_VERSION,
        "task_id":          task["task_id"],
        "model":            model,
        "timestamp":        datetime.utcnow().isoformat(),
        "tool_call_log":    tool_call_log,
        "total_tool_calls": len(tool_call_log),
        "final_text":       final_text,
        "parsed_plan":      parsed_plan,
        "parse_success":    parsed_plan is not None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DRY RUN — stub result for testing evaluator without API
# ─────────────────────────────────────────────────────────────────────────────

def run_dry(task: dict, model: str) -> dict:
    """Return a minimal stub result — useful for pipeline testing."""
    return {
        "schema_version":   RESULT_SCHEMA_VERSION,
        "task_id":          task["task_id"],
        "model":            model,
        "timestamp":        datetime.utcnow().isoformat(),
        "tool_call_log":    [],
        "total_tool_calls": 0,
        "final_text":       "",
        "parsed_plan":      None,
        "parse_success":    False,
        "dry_run":          True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def load_task(task_id: str, city: str = None) -> dict:
    """Load task by ID — searches city directories then legacy path."""
    # City-specific search
    if city:
        _root = DATA_DIR / "cities" / city.lower() / "tasks"
        if _root.exists():
            for f in _root.rglob(f"{task_id}.json"):
                if "agent_log" not in str(f):
                    return json.loads(f.read_text())
    # Legacy flat path
    path = DATA_DIR / "tasks" / f"{task_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    # Scan all cities
    _cities = DATA_DIR / "cities"
    if _cities.exists():
        for f in _cities.rglob(f"{task_id}.json"):
            if "agent_log" not in str(f):
                return json.loads(f.read_text())
    raise FileNotFoundError(f"Task not found: {task_id}")

def save_result(result: dict, out_dir: Path = RESULTS_DIR):
    fname = f"{result['task_id']}__{result['model'].replace('/', '_')}.json"
    out_path = out_dir / fname
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  ✓ Saved: {out_path}")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task",    required=True, help="Task ID e.g. par_easy_001")
    parser.add_argument("--model",   default="claude-sonnet-4-20250514")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args()

    task = load_task(args.task)
    out_dir = Path(args.out_dir) if args.out_dir else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run or not args.api_key:
        print(f"Dry run: {args.task} / {args.model}")
        result = run_dry(task, args.model)
    else:
        if not HAS_ANTHROPIC:
            print("anthropic package not installed. Run: pip install anthropic")
            sys.exit(1)
        print(f"Running: {args.task} / {args.model}")
        result = run_agent(task, args.model, args.api_key)
        print(f"  Tool calls: {result['total_tool_calls']}")
        print(f"  Plan parsed: {result['parse_success']}")

    save_result(result, out_dir)