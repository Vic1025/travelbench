"""
agents/base_runner.py

Shared utilities for all TravelBench model runners:
  - Task loading
  - Result schema
  - Plan parsing
  - Result caching layer
  - Analysis report generation

Each model-specific runner (runner.py, gpt_runner.py, gemini_runner.py) imports
from here and implements only its own API-specific call loop.
"""

import json, re, hashlib, time
from pathlib import Path
from datetime import datetime, timezone

ROOT       = Path(__file__).parent.parent
DATA_DIR   = ROOT / "data"
RESULTS_DIR = ROOT / "results"
CACHE_DIR  = ROOT / ".cache" / "agent_results"
RESULTS_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

RESULT_SCHEMA_VERSION = "1.1"
MAX_TOOL_ROUNDS = 20

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT  (shared across all model runners)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert travel planning assistant. Your task is to create a detailed, day-by-day travel itinerary using the available tools.

## Tools Available

- **search_yelp** — search the venue directory by keyword or category. Returns names, hours,
  labels, and official URL where available. Hours are staff-registered and may occasionally
  be outdated.

- **search_blogs_and_forums** — search travel blog posts and forum threads. Use the `query`
  parameter for broad discovery, or `venue_name` to find everything written about a specific
  place.

- **get_official_site** — fetch authoritative data for a venue: exact hours, ticket
  availability by date, full regulations, and full label list. This is the ONLY source
  for ticket availability — always call this for venues that require booking or timed entry.
  Pass the venue_id from search results, not the venue name.

- **get_travel_time** — returns travel time between two venues by a given mode.
  Modes: walking (default), transit, taxi, bike.
  Pass exact venue_id values (e.g. "par_r03"), not venue names.
  If the gap between two consecutive activities minus travel time is less than 30 minutes,
  add a rest/leisure stop between them.
  Use mode="transit" or mode="taxi" if walking time exceeds 30 minutes.

- **get_weather_forecast** — returns weather forecast for a city and date.
  Always call this if the plan includes outdoor venues.

- **find_nearby_venues** — find venues within walking distance of an anchor venue.
  Use this when planning meals around a fixed attraction, or scouting options within
  a neighbourhood. Returns walk time from travel_matrix.

## Regulation Awareness
When a user mentions travelling with pets, needing wheelchair access, dietary restrictions,
dress code concerns, or any other special requirement — check every venue for compliance.
If a venue's policy cannot be confirmed from sources, add a flag in the activity's
`flags` field: e.g. "pet_policy_unconfirmed — please verify before visiting".

## Output Format
After completing your research, output your final plan as a JSON object wrapped in
<final_plan> tags. The JSON must be valid.

<final_plan>
{
  "task_id": "string",
  "city": "string",
  "total_estimated_cost_local": 0,
  "days": [
    {
      "day": 1,
      "day_of_week": "fri",
      "activities": [
        {
          "time_start": "HH:MM",
          "time_end": "HH:MM",
          "venue_id": "exact_id_from_search_results",
          "venue_name": "string",
          "activity_type": "visit|meal|leisure|shopping",
          "estimated_cost_local": 0,
          "travel_mode": "walking|transit|taxi|bike",
          "flags": ["optional notes or warnings"]
        }
      ]
    }
  ]
}
</final_plan>

Be thorough in your research. Check multiple sources. Verify hours, availability, and
regulations before committing to any venue.
"""

# ─────────────────────────────────────────────────────────────────────────────
# TOOL SCHEMAS — OpenAI-compatible format (used by GPT and Gemini runners)
# ─────────────────────────────────────────────────────────────────────────────

OPENAI_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_yelp",
            "description": "Search the venue directory by keyword, category, or city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":    {"type": "string", "description": "Search terms (name, cuisine, category)"},
                    "city":     {"type": "string", "description": "City code, e.g. 'paris'"},
                    "category": {"type": "string", "description": "Optional category filter"},
                },
                "required": ["query", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_blogs_and_forums",
            "description": "Search travel blogs and forum posts for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":      {"type": "string", "description": "Free-text search query"},
                    "city":       {"type": "string", "description": "City code, e.g. 'paris'"},
                    "venue_name": {"type": "string", "description": "Optional: filter by venue name"},
                },
                "required": ["query", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_official_site",
            "description": "Fetch authoritative data for a venue: hours, ticket availability, regulations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "venue_id": {"type": "string", "description": "Exact venue_id from search results"},
                    "city":     {"type": "string", "description": "City code, e.g. 'paris'"},
                },
                "required": ["venue_id", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_travel_time",
            "description": "Get travel time between two venues by mode (walking/transit/taxi/bike).",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_venue_id": {"type": "string", "description": "Departure venue_id"},
                    "to_venue_id":   {"type": "string", "description": "Destination venue_id"},
                    "city":          {"type": "string", "description": "City code, e.g. 'paris'"},
                    "mode":          {"type": "string", "enum": ["walking", "transit", "taxi", "bike"],
                                     "description": "Travel mode (default: walking)"},
                },
                "required": ["from_venue_id", "to_venue_id", "city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": "Get weather forecast for a city on a specific date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City code, e.g. 'paris'"},
                    "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                },
                "required": ["city", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_nearby_venues",
            "description": "Find venues within walking distance of an anchor venue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "anchor_venue_id":  {"type": "string", "description": "Centre venue_id"},
                    "city":             {"type": "string", "description": "City code, e.g. 'paris'"},
                    "max_walk_minutes": {"type": "integer", "description": "Walk-time radius in minutes"},
                    "category":         {"type": "string", "description": "Optional category filter"},
                },
                "required": ["anchor_venue_id", "city", "max_walk_minutes"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# TASK LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_task(task_id: str) -> dict:
    """Load a task JSON by ID."""
    path = DATA_DIR / "tasks" / f"{task_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Task not found: {task_id}")
    return json.loads(path.read_text())


def build_user_message(task: dict) -> str:
    """Build the initial user message from a task."""
    return (
        f"{task['public_input']['query']}\n\n"
        f"About me: {task['public_input']['user_profile']}\n\n"
        f"Trip details: {task['days']} day(s) starting {task['start_date']} "
        f"({task['start_day_of_week'].capitalize()})."
    )


# ─────────────────────────────────────────────────────────────────────────────
# PLAN PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_final_plan(text: str) -> dict | None:
    """Extract and parse the <final_plan>...</final_plan> JSON block."""
    match = re.search(r"<final_plan>(.*?)</final_plan>", text, re.DOTALL)
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try stripping markdown fences
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# RESULT SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

def build_result(task_id: str, model: str, tool_call_log: list,
                 final_text: str, error: str | None = None) -> dict:
    """Assemble the standard result dict."""
    parsed = parse_final_plan(final_text) if final_text else None
    return {
        "schema_version":   RESULT_SCHEMA_VERSION,
        "task_id":          task_id,
        "model":            model,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "tool_call_log":    tool_call_log,
        "total_tool_calls": len(tool_call_log),
        "final_text":       final_text,
        "parsed_plan":      parsed,
        "parse_success":    parsed is not None,
        "error":            error,
    }


def save_result(result: dict, results_dir: Path | None = None) -> Path:
    """Save result JSON. Returns the path written."""
    d = results_dir or RESULTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    mdl = result["model"].replace("/", "-").replace(":", "-")
    fname = f"{result['task_id']}_{mdl}_{ts}.json"
    path  = d / fname
    path.write_text(json.dumps(result, indent=2))
    return path


def evaluate_and_save(result: dict, results_dir: Path | None = None,
                       api_key: str | None = None,
                       judge_method: str = "code_only") -> tuple[dict, Path]:
    """
    Evaluate a result dict and embed the evaluation scores into the JSON before saving.
    This makes result files self-contained for the dashboard (no re-evaluation needed).

    Returns (evaluation_dict, saved_path).
    """
    try:
        from eval.evaluator import evaluate
        ev = evaluate(result, api_key=api_key, judge_method=judge_method)
        result["_evaluation"] = ev
        result["_task_difficulty"] = ev.get("_task_difficulty", "")
    except Exception as e:
        result["_evaluation_error"] = str(e)

    path = save_result(result, results_dir)
    return result.get("_evaluation", {}), path


# ─────────────────────────────────────────────────────────────────────────────
# CACHING LAYER
# ─────────────────────────────────────────────────────────────────────────────

def _cache_key(model: str, task_id: str, task_hash: str) -> str:
    """Deterministic cache key: model + task_id + hash of task public_input."""
    raw = f"{model}::{task_id}::{task_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _task_hash(task: dict) -> str:
    """Hash the public-facing parts of a task (query + profile + dates)."""
    payload = json.dumps({
        "query":   task["public_input"]["query"],
        "profile": task["public_input"]["user_profile"],
        "start":   task.get("start_date", ""),
        "days":    task.get("days", 0),
    }, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()[:8]


def load_cached_result(model: str, task: dict) -> dict | None:
    """Return a cached result dict if one exists, else None."""
    key  = _cache_key(model, task["task_id"], _task_hash(task))
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def save_cached_result(model: str, task: dict, result: dict) -> None:
    """Write a result to the cache."""
    key  = _cache_key(model, task["task_id"], _task_hash(task))
    path = CACHE_DIR / f"{key}.json"
    try:
        path.write_text(json.dumps(result, indent=2))
    except Exception:
        pass   # cache write failure is non-fatal


def clear_cache(task_id: str | None = None, model: str | None = None) -> int:
    """Delete cache entries. Returns count deleted."""
    deleted = 0
    for f in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text())
            if task_id and data.get("task_id") != task_id:
                continue
            if model and data.get("model") != model:
                continue
            f.unlink()
            deleted += 1
        except Exception:
            pass
    return deleted


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS REPORT
# ─────────────────────────────────────────────────────────────────────────────

def generate_analysis_report(results_dir: Path | None = None) -> str:
    """
    Read all result JSON files in results_dir and generate a markdown analysis report.

    Report sections:
      1. Summary table (model × task × C/F/P/overall scores)
      2. Per-model aggregates (mean ± std for each score tier)
      3. Error frequency table (which constraint types fail most)
      4. Tool usage patterns (avg calls, tool diversity)
      5. Parse success rate per model
    """
    from eval.evaluator import evaluate   # lazy import to avoid circular

    d = results_dir or RESULTS_DIR
    files = sorted(d.glob("*.json"))
    if not files:
        return "No result files found in results directory."

    # Load all results
    records = []
    for f in files:
        try:
            r = json.loads(f.read_text())
            if r.get("schema_version") and r.get("task_id"):
                records.append(r)
        except Exception:
            continue

    if not records:
        return "No valid result files found."

    # Evaluate all (code-only, no LLM)
    evaluated = []
    for r in records:
        try:
            ev = evaluate(r, judge_method="code_only")
            evaluated.append((r, ev))
        except Exception as e:
            evaluated.append((r, {"error": str(e)}))

    # ── 1. Summary table ──────────────────────────────────────────────────
    lines = ["# TravelBench Analysis Report",
             f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             f"Results: {len(evaluated)} runs from {d}",
             "",
             "## 1. Run Summary",
             "",
             "| Task | Model | C | F | P | Overall | Parse |",
             "|------|-------|---|---|---|---------|-------|"]

    by_model: dict[str, list] = {}
    constraint_failures: dict[str, int] = {}
    constraint_totals:   dict[str, int] = {}

    for r, ev in evaluated:
        if "error" in ev:
            continue
        model = r.get("model", "unknown")
        c  = ev.get("c_score", {}).get("score", 0)
        f  = ev.get("f_score", {}).get("score", 0)
        p  = ev.get("p_score", {}).get("score", 0)
        ov = ev.get("overall_score", 0)
        ps = "✓" if r.get("parse_success") else "✗"
        lines.append(
            f"| {r['task_id']} | {model} | {c:.0%} | {f:.0%} | {p:.0%} | **{ov:.0%}** | {ps} |"
        )
        by_model.setdefault(model, []).append((c, f, p, ov))

        # Track constraint failures
        for detail in ev.get("p_score", {}).get("details", []):
            ctype = detail.get("constraint_type", "unknown")
            constraint_totals[ctype] = constraint_totals.get(ctype, 0) + 1
            if detail.get("score", 1.0) < 0.5:
                constraint_failures[ctype] = constraint_failures.get(ctype, 0) + 1

    # ── 2. Per-model aggregates ────────────────────────────────────────────
    import statistics as st
    lines += ["", "## 2. Per-Model Aggregates", "",
              "| Model | N | C mean | F mean | P mean | Overall mean | Overall std |",
              "|-------|---|--------|--------|--------|--------------|-------------|"]

    for model, rows in sorted(by_model.items()):
        n  = len(rows)
        cs = [r[0] for r in rows]
        fs = [r[1] for r in rows]
        ps = [r[2] for r in rows]
        ov = [r[3] for r in rows]
        std = st.stdev(ov) if n > 1 else 0.0
        lines.append(
            f"| {model} | {n} | {st.mean(cs):.1%} | {st.mean(fs):.1%} | "
            f"{st.mean(ps):.1%} | {st.mean(ov):.1%} | ±{std:.1%} |"
        )

    # ── 3. Constraint failure rates ───────────────────────────────────────
    lines += ["", "## 3. Constraint Failure Rates (score < 0.5)", "",
              "| Constraint type | Failures | Total | Fail rate |",
              "|----------------|----------|-------|-----------|"]

    for ctype in sorted(constraint_totals, key=lambda k: -constraint_failures.get(k, 0)):
        total   = constraint_totals[ctype]
        failures = constraint_failures.get(ctype, 0)
        rate = failures / total if total else 0
        lines.append(f"| {ctype} | {failures} | {total} | {rate:.0%} |")

    # ── 4. Tool usage patterns ─────────────────────────────────────────────
    lines += ["", "## 4. Tool Usage Patterns", "",
              "| Model | Avg calls | search_yelp | blogs | official | travel_time | weather | find_nearby |",
              "|-------|-----------|-------------|-------|----------|-------------|---------|-------------|"]

    tool_names = ["search_yelp","search_blogs_and_forums","get_official_site",
                  "get_travel_time","get_weather_forecast","find_nearby_venues"]
    by_model_tools: dict[str, list] = {}
    for r, ev in evaluated:
        if "error" in ev:
            continue
        model = r.get("model", "unknown")
        log = r.get("tool_call_log", [])
        by_model_tools.setdefault(model, []).append(log)

    for model, logs in sorted(by_model_tools.items()):
        n = len(logs)
        avg_calls = sum(len(l) for l in logs) / n
        tool_avgs = []
        for tn in tool_names:
            avg = sum(sum(1 for tc in l if tc.get("tool_name") == tn) for l in logs) / n
            tool_avgs.append(f"{avg:.1f}")
        lines.append(f"| {model} | {avg_calls:.1f} | {' | '.join(tool_avgs)} |")

    # ── 5. Parse success rate ──────────────────────────────────────────────
    lines += ["", "## 5. Parse Success Rate", "",
              "| Model | Runs | Parsed | Rate |",
              "|-------|------|--------|------|"]

    parse_by_model: dict[str, list[bool]] = {}
    for r, _ in evaluated:
        model = r.get("model", "unknown")
        parse_by_model.setdefault(model, []).append(bool(r.get("parse_success")))

    for model, successes in sorted(parse_by_model.items()):
        n    = len(successes)
        good = sum(successes)
        lines.append(f"| {model} | {n} | {good} | {good/n:.0%} |")

    return "\n".join(lines)
