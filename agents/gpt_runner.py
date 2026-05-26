"""
agents/gpt_runner.py

Runs a GPT-4o (or any OpenAI-compatible) agent on a TravelBench task.

Usage:
    python agents/gpt_runner.py --task par_easy_001 --api-key sk-...
    python agents/gpt_runner.py --task par_medium_001 --model gpt-4o-mini --api-key sk-...
    python agents/gpt_runner.py --task par_easy_001 --dry-run
    python agents/gpt_runner.py --task par_easy_001 --use-cache --api-key sk-...
"""

import json, sys, argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agents.base_runner import (
    SYSTEM_PROMPT, OPENAI_TOOL_SCHEMAS, MAX_TOOL_ROUNDS,
    load_task, build_user_message, build_result, save_result,
    load_cached_result, save_cached_result,
    RESULT_SCHEMA_VERSION,
)
from server.mock_tools import dispatch_tool

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


DEFAULT_MODEL = "gpt-4o"


# ─────────────────────────────────────────────────────────────────────────────
# AGENT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_agent(task: dict, model: str, api_key: str) -> dict:
    """Run a GPT agent on a task using the TravelBench mock tools."""
    if not HAS_OPENAI:
        raise ImportError("openai package not installed. Run: pip install openai")

    client = OpenAI(api_key=api_key)
    user_msg = build_user_message(task)

    messages = [
        {"role": "system",  "content": SYSTEM_PROMPT},
        {"role": "user",    "content": user_msg},
    ]

    tool_call_log = []
    round_count   = 0
    final_text    = ""
    error         = None

    try:
        while round_count < MAX_TOOL_ROUNDS:
            round_count += 1

            response = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=messages,
                tools=OPENAI_TOOL_SCHEMAS,
                tool_choice="auto",
            )

            choice  = response.choices[0]
            message = choice.message
            messages.append(message.model_dump(exclude_unset=True))

            if choice.finish_reason == "stop" or not message.tool_calls:
                final_text = message.content or ""
                break

            # Process tool calls
            tool_results_msgs = []
            for tc in message.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_input = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_input = {}

                tool_output = dispatch_tool(tool_name, tool_input)

                tool_call_log.append({
                    "round":       round_count,
                    "tool_name":   tool_name,
                    "tool_input":  tool_input,
                    "tool_output": tool_output,
                })

                tool_results_msgs.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      json.dumps(tool_output),
                })

            messages.extend(tool_results_msgs)

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
    parser = argparse.ArgumentParser(description="TravelBench GPT runner")
    parser.add_argument("--task",      required=True, help="Task ID, e.g. par_easy_001")
    parser.add_argument("--model",     default=DEFAULT_MODEL, help=f"OpenAI model (default: {DEFAULT_MODEL})")
    parser.add_argument("--api-key",   default=None, help="OpenAI API key (or set OPENAI_API_KEY env var)")
    parser.add_argument("--dry-run",   action="store_true", help="Skip API call, write stub result")
    parser.add_argument("--use-cache", action="store_true", help="Return cached result if available")
    parser.add_argument("--out-dir",   default=None, help="Results directory (default: results/)")
    args = parser.parse_args()

    import os
    api_key = args.api_key or os.environ.get("OPENAI_API_KEY")

    task = load_task(args.task)
    print(f"Task: {task['task_id']} | Model: {args.model} | Difficulty: {task['difficulty']}")

    if args.use_cache and not args.dry_run:
        cached = load_cached_result(args.model, task)
        if cached:
            print(f"Cache hit — skipping API call.")
            out_dir = Path(args.out_dir) if args.out_dir else None
            path = save_result(cached, out_dir)
            print(f"Saved: {path}")
            return

    if args.dry_run:
        result = run_dry(task, args.model)
    else:
        if not api_key:
            print("Error: --api-key or OPENAI_API_KEY required.")
            sys.exit(1)
        if not HAS_OPENAI:
            print("Error: openai package not installed. Run: pip install openai")
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
