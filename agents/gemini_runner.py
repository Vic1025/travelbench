"""
agents/gemini_runner.py

Runs a Google Gemini agent on a TravelBench task.

Gemini supports OpenAI-compatible tool calling via the google-generativeai SDK.
The agent loop mirrors gpt_runner.py but uses the Gemini API.

Usage:
    python agents/gemini_runner.py --task par_easy_001 --api-key AIza...
    python agents/gemini_runner.py --task par_medium_001 --model gemini-1.5-pro --api-key AIza...
    python agents/gemini_runner.py --task par_easy_001 --dry-run
    python agents/gemini_runner.py --task par_easy_001 --use-cache --api-key AIza...
"""

import json, sys, argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agents.base_runner import (
    SYSTEM_PROMPT, MAX_TOOL_ROUNDS,
    load_task, build_user_message, build_result, save_result,
    load_cached_result, save_cached_result,
    RESULT_SCHEMA_VERSION,
)
from server.mock_tools import dispatch_tool

try:
    import google.generativeai as genai
    from google.generativeai.types import FunctionDeclaration, Tool
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False


DEFAULT_MODEL = "gemini-1.5-flash"

# ─────────────────────────────────────────────────────────────────────────────
# GEMINI TOOL DECLARATIONS
# ─────────────────────────────────────────────────────────────────────────────

GEMINI_TOOL_DECLARATIONS = [
    {
        "name": "search_yelp",
        "description": "Search the venue directory by keyword, category, or city.",
        "parameters": {
            "type": "object",
            "properties": {
                "query":    {"type": "string", "description": "Search terms"},
                "city":     {"type": "string", "description": "City code e.g. 'paris'"},
                "category": {"type": "string", "description": "Optional category filter"},
            },
            "required": ["query", "city"],
        },
    },
    {
        "name": "search_blogs_and_forums",
        "description": "Search travel blogs and forum posts.",
        "parameters": {
            "type": "object",
            "properties": {
                "query":      {"type": "string"},
                "city":       {"type": "string"},
                "venue_name": {"type": "string", "description": "Optional venue name filter"},
            },
            "required": ["query", "city"],
        },
    },
    {
        "name": "get_official_site",
        "description": "Fetch authoritative venue data: hours, ticket availability, regulations.",
        "parameters": {
            "type": "object",
            "properties": {
                "venue_id": {"type": "string"},
                "city":     {"type": "string"},
            },
            "required": ["venue_id", "city"],
        },
    },
    {
        "name": "get_travel_time",
        "description": "Get travel time between two venues. Modes: walking, transit, taxi, bike.",
        "parameters": {
            "type": "object",
            "properties": {
                "from_venue_id": {"type": "string"},
                "to_venue_id":   {"type": "string"},
                "city":          {"type": "string"},
                "mode":          {"type": "string", "enum": ["walking", "transit", "taxi", "bike"]},
            },
            "required": ["from_venue_id", "to_venue_id", "city"],
        },
    },
    {
        "name": "get_weather_forecast",
        "description": "Weather forecast for a city on a date.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["city", "date"],
        },
    },
    {
        "name": "find_nearby_venues",
        "description": "Find venues within walking distance of an anchor venue.",
        "parameters": {
            "type": "object",
            "properties": {
                "anchor_venue_id":  {"type": "string"},
                "city":             {"type": "string"},
                "max_walk_minutes": {"type": "integer"},
                "category":         {"type": "string"},
            },
            "required": ["anchor_venue_id", "city", "max_walk_minutes"],
        },
    },
]


def _build_gemini_tools():
    """Build Gemini Tool objects from declarations."""
    if not HAS_GEMINI:
        return None
    declarations = [FunctionDeclaration(**d) for d in GEMINI_TOOL_DECLARATIONS]
    return [Tool(function_declarations=declarations)]


# ─────────────────────────────────────────────────────────────────────────────
# AGENT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_agent(task: dict, model: str, api_key: str) -> dict:
    """Run a Gemini agent on a task using TravelBench mock tools."""
    if not HAS_GEMINI:
        raise ImportError(
            "google-generativeai package not installed. "
            "Run: pip install google-generativeai"
        )

    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(
        model_name=model,
        system_instruction=SYSTEM_PROMPT,
        tools=_build_gemini_tools(),
    )

    user_msg      = build_user_message(task)
    chat          = gemini_model.start_chat()
    tool_call_log = []
    round_count   = 0
    final_text    = ""
    error         = None

    try:
        response = chat.send_message(user_msg)

        while round_count < MAX_TOOL_ROUNDS:
            round_count += 1

            # Check for function calls in response
            has_tool_call = False
            tool_response_parts = []

            for part in response.parts:
                if hasattr(part, "function_call") and part.function_call.name:
                    has_tool_call   = True
                    fn              = part.function_call
                    tool_name       = fn.name
                    tool_input      = dict(fn.args)

                    tool_output = dispatch_tool(tool_name, tool_input)

                    tool_call_log.append({
                        "round":       round_count,
                        "tool_name":   tool_name,
                        "tool_input":  tool_input,
                        "tool_output": tool_output,
                    })

                    tool_response_parts.append(
                        genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=tool_name,
                                response={"result": json.dumps(tool_output)},
                            )
                        )
                    )

            if not has_tool_call:
                # End of tool loop — extract text
                final_text = response.text
                break

            # Send all tool results back
            response = chat.send_message(tool_response_parts)

    except Exception as e:
        error = str(e)

    return build_result(task["task_id"], model, tool_call_log, final_text, error)


# ─────────────────────────────────────────────────────────────────────────────
# DRY RUN
# ─────────────────────────────────────────────────────────────────────────────

def run_dry(task: dict, model: str) -> dict:
    from datetime import datetime, timezone
    return {
        "schema_version":   RESULT_SCHEMA_VERSION,
        "task_id":          task["task_id"],
        "model":            model,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "tool_call_log":    [],
        "total_tool_calls": 0,
        "final_text":       "(dry run — no API call made)",
        "parsed_plan":      None,
        "parse_success":    False,
        "error":            None,
        "dry_run":          True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TravelBench Gemini runner")
    parser.add_argument("--task",      required=True, help="Task ID e.g. par_easy_001")
    parser.add_argument("--model",     default=DEFAULT_MODEL, help=f"Gemini model (default: {DEFAULT_MODEL})")
    parser.add_argument("--api-key",   default=None, help="Google AI API key (or set GOOGLE_API_KEY)")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--use-cache", action="store_true")
    parser.add_argument("--out-dir",   default=None)
    args = parser.parse_args()

    import os
    api_key = args.api_key or os.environ.get("GOOGLE_API_KEY")

    task = load_task(args.task)
    print(f"Task: {task['task_id']} | Model: {args.model} | Difficulty: {task['difficulty']}")

    if args.use_cache and not args.dry_run:
        cached = load_cached_result(args.model, task)
        if cached:
            print("Cache hit — skipping API call.")
            out_dir = Path(args.out_dir) if args.out_dir else None
            path = save_result(cached, out_dir)
            print(f"Saved: {path}")
            return

    if args.dry_run:
        result = run_dry(task, args.model)
    else:
        if not api_key:
            print("Error: --api-key or GOOGLE_API_KEY required.")
            sys.exit(1)
        if not HAS_GEMINI:
            print("Error: google-generativeai not installed. Run: pip install google-generativeai")
            sys.exit(1)
        result = run_agent(task, args.model, api_key)

    out_dir = Path(args.out_dir) if args.out_dir else None
    path = save_result(result, out_dir)
    print(f"Tool calls: {result['total_tool_calls']} | Parse: {'✓' if result['parse_success'] else '✗'}")
    print(f"Saved: {path}")

    if args.use_cache and not args.dry_run:
        save_cached_result(args.model, task, result)


if __name__ == "__main__":
    main()
