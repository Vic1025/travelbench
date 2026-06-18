"""
run_benchmark.py — TravelBench evaluation pipeline

Runs a model against N tasks, collects tool call logs and final plans,
scores each with the evaluator, and writes a results report.

Usage:
  # Run 2 Paris tasks with Anthropic model
  python run_benchmark.py \\
    --tasks par_easy_001 par_medium_002 \\
    --model claude-haiku-4-5-20251001 \\
    --api-key YOUR_KEY \\
    --provider anthropic

  # Run all Paris tasks with OpenAI model
  python run_benchmark.py \\
    --tasks par_easy_001 par_medium_002 \\
    --model gpt-4o-mini \\
    --api-key YOUR_KEY \\
    --provider openai

  # Doubao (Volcano Engine / 火山引擎)
  python run_benchmark.py \\
    --city london --run-name test_70 \\
    --model doubao-pro-32k \\
    --api-key YOUR_ARK_KEY

  # Doubao via deployment endpoint ID (from Volcano Engine console)
  python run_benchmark.py \\
    --city london --run-name test_70 \\
    --model ep-20250503xxxxxx-yyyyy \\
    --api-key YOUR_ARK_KEY

  # Kimi K2 (official platform: platform.moonshot.ai — NOT Volcano Engine)
  #   API key: platform.moonshot.ai/console/api-keys  →  set MOONSHOT_API_KEY
  python run_benchmark.py \\
    --city london --run-name test_70 \\
    --model kimi-k2.6 \\
    --api-key YOUR_MOONSHOT_KEY

  # GLM (Zhipu AI / 智谱AI)
  python run_benchmark.py \\
    --city london --run-name test_70 \\
    --model glm-4 \\
    --api-key YOUR_ZHIPU_KEY

  # Dry-run to check setup (no API calls)
  python run_benchmark.py --tasks par_easy_001 par_medium_002 --dry-run

Token budget estimate (printed before each run):
  easy:   ~8,000–12,000 tokens  (~$0.002–0.05 depending on model)
  medium: ~12,000–18,000 tokens (~$0.004–0.08)
  hard:   ~20,000–35,000 tokens (~$0.008–0.15)
  Two-task run: multiply above by 2
"""

import json
import time
import argparse
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from server.mock_tools import (
    TOOL_SCHEMAS, dispatch_tool, set_run_name, set_clean_environment, set_arm,
)
from eval.evaluator import evaluate, load_task

# ─────────────────────────────────────────────────────────────────────────────
# TOKEN ESTIMATOR
# ─────────────────────────────────────────────────────────────────────────────

def estimate_tokens(tasks: list[dict]) -> dict:
    """
    Estimate token usage for a list of tasks.
    Returns per-task and total estimates.
    """
    SYSTEM_TOKENS    = 850   # system prompt + tool schemas
    TOOL_CALL_BUDGET = {     # avg tokens per tool call (input + output combined)
        "easy":   700,
        "medium": 900,
        "hard":   1100,
    }
    PLAN_TOKENS = {
        "easy":   900,   # ~300 input history + 600 output
        "medium": 1400,
        "hard":   2200,
    }
    AVG_CALLS = {"easy": 8, "medium": 12, "hard": 18}

    rows = []
    total_in = total_out = 0

    for task in tasks:
        diff   = task.get("difficulty", "medium")
        calls  = AVG_CALLS.get(diff, 12)
        t_call = TOOL_CALL_BUDGET.get(diff, 900)
        t_plan = PLAN_TOKENS.get(diff, 1400)

        # Accumulating context: first call is short, last is long
        # Model: avg token growth per turn ~200
        context_growth = calls * (calls - 1) // 2 * 50  # triangular accumulation
        est_in  = SYSTEM_TOKENS + calls * 400 + context_growth
        est_out = calls * (t_call - 400) + t_plan

        total_in  += est_in
        total_out += est_out
        rows.append({
            "task_id":    task["task_id"],
            "difficulty": diff,
            "est_calls":  calls,
            "est_input":  est_in,
            "est_output": est_out,
            "est_total":  est_in + est_out,
        })

    return {
        "per_task": rows,
        "total_input":  total_in,
        "total_output": total_out,
        "total_tokens": total_in + total_out,
    }


def print_token_budget(budget: dict, model: str):
    """Print a clear pre-run cost estimate."""
    # Approximate $/1M token pricing (input/output) as of 2026
    PRICING = {
        # Anthropic
        "claude-haiku-4-5-20251001":     (0.80,  4.00),
        "claude-sonnet-4-20250514":      (3.00, 15.00),
        "claude-sonnet-4-6":             (3.00, 15.00),
        "claude-opus-4-6":               (15.0, 75.00),
        # OpenAI
        "gpt-4o":                        (2.50, 10.00),
        "gpt-4o-mini":                   (0.15,  0.60),
        "gpt-5.4":                       (3.00, 15.00),
        "o1-mini":                       (3.00, 12.00),
        "o3-mini":                       (1.10,  4.40),
        # Google
        "gemini-1.5-flash":              (0.075, 0.30),
        "gemini-1.5-pro":                (1.25,  5.00),
        "gemini-2.0-flash":              (0.10,  0.40),
        "gemini-3-flash-preview":        (0.10,  0.40),
        "gemini-3.1-pro-preview":        (1.25,  5.00),
        # DeepSeek
        "deepseek-chat":                 (0.27,  1.10),
        "deepseek-reasoner":             (0.55,  2.19),
        # ── Volcano Engine / Doubao (火山引擎/字节跳动) ────────────────────────
        # Prices converted from ¥/M tokens at ~7.25 ¥/$
        "doubao-pro-32k":                (0.11,  0.11),
        "doubao-pro-128k":               (0.55,  0.55),
        "doubao-lite-32k":               (0.02,  0.02),
        "doubao-1-5-pro-32k":            (0.11,  0.11),
        "doubao-1-5-pro-256k":           (0.83,  0.83),
        # ── Kimi / Moonshot AI (月之暗面) — official: platform.moonshot.ai ────
        # K2.6 series (current recommended)
        "kimi-k2.6":                     (1.00,  3.00),
        "kimi-k2.5":                     (0.60,  2.50),
        # K2 series (deprecated May 25 2026 — use kimi-k2.6 instead)
        "kimi-k2-0905-preview":          (0.60,  2.50),
        "kimi-k2-0711-preview":          (0.60,  2.50),
        "kimi-k2-turbo-preview":         (0.30,  1.25),   # high-speed, 60-100 tok/s
        "kimi-k2-thinking":              (1.00,  3.50),   # reasoning model
        "kimi-k2-thinking-turbo":        (0.50,  1.75),
        # Legacy moonshot-v1 series
        "moonshot-v1-8k":                (0.17,  0.17),
        "moonshot-v1-32k":               (0.34,  0.34),
        "moonshot-v1-128k":              (1.38,  1.38),
        # ── GLM / Zhipu AI (智谱AI) ──────────────────────────────────────────
        "glm-4":                         (0.14,  0.14),
        "glm-4-flash":                   (0.00,  0.00),   # free tier
        "glm-4-air":                     (0.01,  0.01),
        "glm-z1-flash":                  (0.00,  0.00),   # free reasoning tier
        "glm-z1-air":                    (0.07,  0.07),
        "glm-z1-airx":                   (0.14,  0.14),
    }

    in_m  = budget["total_input"]  / 1_000_000
    out_m = budget["total_output"] / 1_000_000

    if model in PRICING:
        p_in, p_out = PRICING[model]
        cost = in_m * p_in + out_m * p_out
        cost_str = f"~${cost:.4f}"
    else:
        cost_str = "unknown (model not in pricing table)"

    print(f"\n{'─'*55}")
    print(f"TOKEN BUDGET ESTIMATE — {model}")
    print(f"{'─'*55}")
    for row in budget["per_task"]:
        print(f"  {row['task_id']:25s} ({row['difficulty']:6s}) "
              f"~{row['est_total']:,} tokens  (~{row['est_calls']} tool calls)")
    print(f"  {'─'*50}")
    print(f"  Total input:  ~{budget['total_input']:,}")
    print(f"  Total output: ~{budget['total_output']:,}")
    print(f"  Total tokens: ~{budget['total_tokens']:,}")
    print(f"  Est. cost:    {cost_str}")
    print(f"{'─'*55}\n")


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────────────────────

def _get_system_prompt(task: dict, max_tool_calls: int = 30) -> str:
    """Build system prompt using the runner's city-aware builder."""
    from agents.runner import _build_system_prompt
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
    return _build_system_prompt(task, _city_config, max_tool_calls=max_tool_calls)


# ─────────────────────────────────────────────────────────────────────────────
# ANTHROPIC CLIENT
# ─────────────────────────────────────────────────────────────────────────────

def run_anthropic(task: dict, model: str, api_key: str,
                  max_tool_calls: int = 30) -> dict:
    """Run one task using the Anthropic API (native tool_use format)."""
    import anthropic

    client   = anthropic.Anthropic(api_key=api_key)
    city     = task["city"]
    query    = task["public_input"]["query"]
    profile  = task["public_input"].get("user_profile", "")
    dates    = _build_date_context(task)

    user_msg = (
        f"Trip request:\n{query}\n\n"
        + (f"Traveller profile:\n{profile}\n\n" if profile else "")
        + f"Travel dates: {dates}"
    )

    messages       = [{"role": "user", "content": user_msg}]
    tool_call_log  = []
    think_log      = []
    transcript     = [{"role": "user", "content": user_msg}]
    final_text     = ""
    input_tokens   = 0
    output_tokens  = 0
    calls_made     = 0

    # Convert TOOL_SCHEMAS to Anthropic format
    tools = [
        {
            "name":         t["name"],
            "description":  t["description"],
            "input_schema": t["input_schema"],
        }
        for t in TOOL_SCHEMAS
    ]

    while calls_made < max_tool_calls:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=_get_system_prompt(task, max_tool_calls=max_tool_calls),
            tools=tools,
            messages=messages,
        )

        input_tokens  += response.usage.input_tokens
        output_tokens += response.usage.output_tokens

        # Collect text and tool uses from response
        text_parts = []
        tool_uses  = []
        block_types = []
        for block in response.content:
            block_types.append(block.type)
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        final_text = "\n".join(text_parts)
        # Debug: surface block-type composition + stop_reason per turn so we can see
        # whether the model is emitting thinking/tool_use/text and in what mix.
        print(f"    [turn {calls_made}] blocks={block_types} text_chars={len(final_text)} stop={response.stop_reason}")

        # Stop if no tool calls
        if not tool_uses or response.stop_reason == "end_turn":
            transcript.append({"role": "assistant", "content": final_text})
            break

        # Add assistant turn
        messages.append({"role": "assistant", "content": response.content})
        transcript.append({
            "role": "assistant",
            "content": final_text,
            "tool_calls": [{"name": tu.name, "input": tu.input} for tu in tool_uses],
        })

        # Execute all tool calls in this turn
        tool_results = []
        tool_result_log = []
        for tu in tool_uses:
            tool_output = dispatch_tool(tu.name, tu.input)
            log_entry   = {
                "tool_name":   tu.name,
                "tool_input":  tu.input,
                "tool_output": tool_output,
            }
            # THINK is free — log to think_log, don't count toward budget
            if tu.name == "THINK":
                think_log.append({"thought": tu.input.get("thought", "")})
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tu.id,
                    "content":     json.dumps(tool_output),
                })
                continue

            tool_call_log.append(log_entry)
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": tu.id,
                "content":     json.dumps(tool_output),
            })
            tool_result_log.append({
                "tool": tu.name,
                "input": tu.input,
            })
            calls_made += 1

        messages.append({"role": "user", "content": tool_results})
        transcript.append({"role": "tool_results", "results": tool_result_log})

    # Cap reached — give one final uncapped call so model can write <final_plan>
    if calls_made >= max_tool_calls and "<final_plan>" not in final_text and "<｜DSML｜final_plan>" not in final_text:
        messages.append({"role": "user", "content":
            "You have used your full tool call budget. "
            "Based on all the research gathered above, write your final plan "
            "now inside <final_plan> tags."})
        try:
            # Stream the rescue call: max_tokens > ~16k requires streaming per Anthropic SDK
            # (avoid silent rejection on long final plans for annotated 3-day itineraries)
            with client.messages.stream(
                model=model, max_tokens=32768,
                system=_get_system_prompt(task, max_tool_calls=max_tool_calls),
                messages=messages,
            ) as stream:
                final_resp = stream.get_final_message()
            final_text = "\n".join(
                b.text for b in final_resp.content if b.type == "text"
            )
            input_tokens  += final_resp.usage.input_tokens
            output_tokens += final_resp.usage.output_tokens
            transcript.append({"role": "assistant", "content": final_text})
            print(f"    [rescue] stop_reason={final_resp.stop_reason} "
                  f"out_tokens={final_resp.usage.output_tokens} "
                  f"text_len={len(final_text)} "
                  f"has_tag={'<final_plan>' in final_text}")
        except Exception as _e:
            print(f"    [rescue FAILED] {type(_e).__name__}: {_e}")

    return {
        "task_id":      task["task_id"],
        "model":        model,
        "provider":     "anthropic",
        "tool_call_log": tool_call_log,
        "think_log":    think_log,
        "raw_response": final_text,
        "transcript":   transcript,
        "calls_made":   calls_made,
        "max_tool_calls": max_tool_calls,
        "usage": {
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "total_tokens":  input_tokens + output_tokens,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# OPENAI-COMPATIBLE CLIENT
# Handles: OpenAI, DeepSeek, Gemini, Doubao/Volcano Engine, Kimi/Moonshot, GLM
# ─────────────────────────────────────────────────────────────────────────────

def run_openai(task: dict, model: str, api_key: str,
               base_url: str = None,
               max_tool_calls: int = 30) -> dict:
    """Run one task using the OpenAI-compatible API."""
    from openai import OpenAI

    client  = OpenAI(api_key=api_key, base_url=base_url)
    city    = task["city"]
    query   = task["public_input"]["query"]
    profile = task["public_input"].get("user_profile", "")
    dates   = _build_date_context(task)

    user_content = (
        f"Trip request:\n{query}\n\n"
        + (f"Traveller profile:\n{profile}\n\n" if profile else "")
        + f"Travel dates: {dates}"
    )

    # Convert TOOL_SCHEMAS to OpenAI function format
    tools = [
        {
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t["description"],
                "parameters":  t["input_schema"],
            },
        }
        for t in TOOL_SCHEMAS
    ]

    messages      = [
        {"role": "system", "content": _get_system_prompt(task, max_tool_calls=max_tool_calls)},
        {"role": "user",   "content": user_content},
    ]
    tool_call_log = []
    think_log     = []
    transcript    = [{"role": "user", "content": user_content}]
    final_text    = ""
    input_tokens  = 0
    output_tokens = 0
    calls_made    = 0

    # Gemini 3.x uses dynamic thinking by default, attaching thoughtSignatures to
    # each function call. reasoning_effort="low" minimises thinking tokens.
    # model_dump() on the message preserves thought signatures in history.
    _is_gemini = "gemini" in model.lower()

    while calls_made < max_tool_calls:
        create_kwargs = dict(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_completion_tokens=4096,
        )
        if _is_gemini:
            create_kwargs["reasoning_effort"] = "low"
        response = client.chat.completions.create(**create_kwargs)

        msg    = response.choices[0].message
        usage  = response.usage
        if usage:
            input_tokens  += usage.prompt_tokens
            output_tokens += usage.completion_tokens

        final_text = msg.content or ""

        # Stop if no tool calls
        if not msg.tool_calls or response.choices[0].finish_reason == "stop":
            transcript.append({"role": "assistant", "content": final_text})
            break

        tc_list = [{"name": tc.function.name,
                     "input": json.loads(tc.function.arguments) if tc.function.arguments else {}}
                    for tc in msg.tool_calls]
        transcript.append({
            "role": "assistant",
            "content": final_text or "",
            "tool_calls": tc_list,
        })

        # Use the original message object to preserve all provider-specific fields
        # (e.g. Gemini 3.1 thoughtSignature required for multi-turn function calling)
        try:
            # model_dump() preserves all fields including thought signatures
            messages.append(msg.model_dump(exclude_none=True))
        except AttributeError:
            # Fallback for older SDK versions
            messages.append({"role": "assistant", "content": msg.content,
                             "tool_calls": [
                                 {"id": tc.id, "type": "function",
                                  "function": {"name": tc.function.name,
                                               "arguments": tc.function.arguments}}
                                 for tc in msg.tool_calls
                             ]})

        tool_result_log = []
        for tc in msg.tool_calls:
            try:
                tool_input = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                tool_input = {}

            tool_output = dispatch_tool(tc.function.name, tool_input)
            # THINK is free — log to think_log, don't count toward budget
            if tc.function.name == "THINK":
                think_log.append({"thought": tool_input.get("thought", "")})
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      json.dumps(tool_output),
                })
                continue

            tool_call_log.append({
                "tool_name":   tc.function.name,
                "tool_input":  tool_input,
                "tool_output": tool_output,
            })
            messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      json.dumps(tool_output),
            })
            tool_result_log.append({
                "tool": tc.function.name,
                "input": tool_input,
            })
            calls_made += 1

        transcript.append({"role": "tool_results", "results": tool_result_log})

    # Cap reached — give one final uncapped call so model can write <final_plan>
    if calls_made >= max_tool_calls and "<final_plan>" not in final_text and "<｜DSML｜final_plan>" not in final_text:
        messages.append({"role": "user", "content":
            "You have used your full tool call budget. "
            "Based on all the research gathered above, write your final plan "
            "now inside <final_plan> tags."})
        try:
            final_resp = client.chat.completions.create(
                model=model, messages=messages, max_completion_tokens=32768,
                tool_choice="none",  # hard-block tool use on final call
            )
            final_text = final_resp.choices[0].message.content or ""
            u2 = final_resp.usage
            if u2:
                input_tokens  += u2.prompt_tokens
                output_tokens += u2.completion_tokens
            transcript.append({"role": "assistant", "content": final_text})
            print(f"    [rescue:openai] finish={final_resp.choices[0].finish_reason} "
                  f"out_tokens={(u2.completion_tokens if u2 else 0)} "
                  f"text_len={len(final_text)} "
                  f"has_tag={'<final_plan>' in final_text}")
        except Exception as _e:
            print(f"    [rescue:openai FAILED] {type(_e).__name__}: {_e}")

    return {
        "task_id":      task["task_id"],
        "model":        model,
        "provider":     "openai",
        "tool_call_log": tool_call_log,
        "think_log":    think_log,
        "raw_response": final_text,
        "transcript":   transcript,
        "calls_made":   calls_made,
        "max_tool_calls": max_tool_calls,
        "usage": {
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "total_tokens":  input_tokens + output_tokens,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# PLAN PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_plan(raw_response: str) -> dict | None:
    """Extract JSON plan from <final_plan> tags, DeepSeek DSML tags, or ```json blocks."""
    import re
    # 1. Primary: <final_plan>...</final_plan>
    m = re.search(r"<final_plan>(.*?)</final_plan>", raw_response, re.DOTALL)
    # 2. DeepSeek DSML: <｜DSML｜final_plan>...</｜DSML｜final_plan>
    if not m:
        m = re.search(r"<｜DSML｜final_plan>(.*?)</｜DSML｜final_plan>", raw_response, re.DOTALL)
    # 3. Gemini / generic: ```json {...} ```
    if not m:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_response, re.DOTALL)
    if not m:
        return None
    raw_json = m.group(1).strip()
    # 4. Strip trailing commas (Gemini/DeepSeek occasionally output non-strict JSON)
    raw_json = re.sub(r",(\s*[}\]])", r"\1", raw_json)
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return None

def run_dry(task: dict, model: str) -> dict:
    """Return a stub result for dry-run mode (no API calls)."""
    city = task["city"]
    days = task.get("days", 1)

    # Minimal valid plan using first venues from Yelp
    from server.mock_tools import tool_search_yelp
    yelp_r = tool_search_yelp("restaurant", city, top_k=2)
    results = yelp_r.get("results", [])

    day_activities = []
    for i, r in enumerate(results[:2]):
        day_activities.append({
            "time_start":         f"{10 + i*3:02d}:00",
            "time_end":           f"{10 + i*3 + 2:02d}:00",
            "venue_id":           r["venue_id"],
            "venue_name":         r["name"],
            "activity_type":      "meal" if i == 0 else "visit",
            "estimated_cost_local": 25,
            "notes":              "stub plan",
        })

    stub_plan = {
        "task_id":                    task["task_id"],
        "city":                       city,
        "days":                       [
            {"day": d+1,
             "day_of_week": ["sat","sun","mon","tue","wed","thu","fri"][d % 7],
             "activities":  day_activities if d == 0 else []}
            for d in range(days)
        ],
        "total_estimated_cost_local":   50,
        "unresolved_flags":           [],
        "planning_notes":             "Dry-run stub plan",
    }

    return {
        "task_id":      task["task_id"],
        "model":        model,
        "provider":     "dry_run",
        "tool_call_log": [
            {"tool_name": "search_yelp",
             "tool_input": {"query": "restaurant", "city": city},
             "tool_output": yelp_r},
            {"tool_name": "search_blogs_and_forums",
             "tool_input": {"query": "best spots", "city": city},
             "tool_output": {"results": []}},
            {"tool_name": "get_travel_time",
             "tool_input": {"from_venue_id": results[0]["venue_id"],
                            "to_venue_id":   results[1]["venue_id"] if len(results)>1 else results[0]["venue_id"],
                            "city":          city},
             "tool_output": {"travel_time_minutes": 12}},
        ] if results else [],
        "raw_response": f"<final_plan>{json.dumps(stub_plan)}</final_plan>",
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    }


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _build_date_context(task: dict) -> str:
    """Build a natural-language date context string for the agent."""
    start  = task.get("start_date", "")
    days   = task.get("days", 1)
    dow    = task.get("start_day_of_week", "")
    # Compute day-of-week from start_date if missing
    if not dow and start:
        from datetime import date as _d
        try:
            dow = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"][_d.fromisoformat(start).weekday()]
        except Exception:
            pass
    if days == 1:
        return f"{dow.title()} {start}" if dow else start
    from datetime import date as _date, timedelta
    try:
        end = (_date.fromisoformat(start) + timedelta(days=days-1)).isoformat()
    except Exception:
        end = start
    return f"{dow.title()} {start} through {end} ({days} days)"


# ─────────────────────────────────────────────────────────────────────────────
# REPORT PRINTER
# ─────────────────────────────────────────────────────────────────────────────

def print_task_result(task: dict, raw_result: dict, scored: dict):
    """Print a clean per-task result block."""
    tid  = task["task_id"]
    diff = task.get("difficulty","?")
    used = raw_result["usage"]["total_tokens"]

    c = scored.get("c_score", {})
    f = scored.get("f_score", {})
    p = scored.get("p_score", {})
    b = scored.get("b_score", {})

    def fmt(score):
        if score is None: return "  N/A"
        return f"{score:5.1%}"

    print(f"\n  ┌─ {tid}  ({diff})  [{used:,} tokens]")
    print(f"  │  Tool calls: {len(raw_result['tool_call_log'])}")
    print(f"  │  Plan parsed: {'yes' if raw_result.get('parsed_plan') else 'NO ← check raw output'}")
    print(f"  │")

    # Surface task-runtime error (e.g. KeyError before the model could respond)
    if raw_result.get("error"):
        print(f"  │  ❌ TASK ERROR: {raw_result['error']}")
        print(f"  └─ COMPOSITE:   N/A   (task did not complete)")
        return

    # Surface evaluator error (so it isn't silently swallowed into N/A)
    if scored.get("error"):
        print(f"  │  ❌ SCORING ERROR: {scored['error']}")
        if scored.get("traceback"):
            print(f"  │  ── traceback ──")
            for line in scored["traceback"].rstrip().splitlines()[-8:]:
                print(f"  │  {line}")
        print(f"  └─ COMPOSITE:   N/A   (evaluator raised — see traceback above)")
        return

    print(f"  │  C-score (process): {fmt(c.get('score'))}  {'✅' if c.get('passed') else '❌'} "
          f"deductions={len(c.get('deductions',[]))} warnings={len(c.get('warnings',[]))}")

    for d in c.get("deductions", [])[:5]:
        amt = d["amount"] if isinstance(d, dict) else d[0]
        desc = d["reason"] if isinstance(d, dict) else d[1]
        print(f"  │    −{amt:.2f}  {str(desc)[:85]}")
    if c.get("warnings"):
        for w in c["warnings"][:3]:
            print(f"  │    ⚠  {w[:90]}")

    if not c.get("passed"):
        print(f"  │  [C gate failed — F/P/B not evaluated]")
        print(f"  │")
        _cm = raw_result.get("calls_made", "?")
        _mx = raw_result.get("max_tool_calls", "?")
        print(f"  │  ── Agent debug ({_cm}/{_mx} tool rounds) ──")
        tcl = raw_result.get("tool_call_log", [])
        if tcl:
            for i, call in enumerate(tcl[:8]):
                tn = call.get("tool_name", "?")
                ti = call.get("tool_input", {})
                brief = ", ".join(f"{k}={str(v)[:25]}" for k, v in list(ti.items())[:3])
                print(f"  │  call {i+1}: {tn}({brief})")
        else:
            print(f"  │  (no tool calls)")
        print(f"  │  ── end debug ──")
    else:
        f_val = f.get("score", 0)
        f_icon = "✅" if f_val >= 0.8 else ("⚠" if f_val >= 0.5 else "❌")
        print(f"  │  F-score (feasibility): {fmt(f_val)}  {f_icon}")
        crit = " ⚠ critical" if f.get("has_critical_issues") else ""
        for d in f.get("deductions", [])[:3]:
            amt = d["amount"] if isinstance(d, dict) else d[0]
            desc = d["reason"] if isinstance(d, dict) else d[1]
            print(f"  │    −{amt:.2f} [{d.get('section','?') if isinstance(d,dict) else '?'}] {str(desc)[:65]}")
        if len(f.get("deductions", [])) > 3:
            print(f"  │    ... +{len(f['deductions'])-3} more")

        print(f"  │  P-score (preferences): {fmt(p.get('score'))}")
        if p.get("code_results"):
            for r in p["code_results"]:
                icon = "✓" if r.get("score",0) >= 0.8 else ("~" if r.get("score",0) >= 0.4 else "✗")
                print(f"  │    {icon} [{r.get('id','?')}] score={r.get('score','?'):.2f}  {r.get('reason','')[:50]}")

        b_score = b.get("score")
        print(f"  │  B-score (bonus):       {fmt(b_score)}")

    print(f"  │")


def print_run_summary(results: list[dict], model: str, total_ms: int):
    """Print a summary table across all tasks."""
    print(f"\n{'═'*60}")
    print(f"RUN SUMMARY — {model}")
    print(f"{'═'*60}")
    print(f"{'Task':<25} {'Diff':6} {'C':>6} {'F':>6} {'P':>6} {'Tokens':>8}")
    print(f"{'─'*60}")

    total_tokens = 0
    for r in results:
        tid   = r["task_id"]
        diff  = r.get("difficulty","?")[:6]
        sc    = r.get("scored", {})

        c_sc  = sc.get("c_score",{}).get("score")
        f_sc  = sc.get("f_score",{}).get("score")
        p_sc  = sc.get("p_score",{}).get("score")
        tok   = r.get("usage",{}).get("total_tokens",0)
        total_tokens += tok

        def s(v): return f"{v:.0%}" if v is not None else "N/A"
        print(f"  {tid:<23} {diff:<6} {s(c_sc):>6} {s(f_sc):>6} {s(p_sc):>6} {tok:>8,}")

    print(f"{'─'*60}")
    print(f"  {'TOTAL':40} {total_tokens:>8,} tokens")
    print(f"  Wall time: {total_ms/1000:.1f}s")
    print(f"{'═'*60}")


# ─────────────────────────────────────────────────────────────────────────────
# PROVIDER DETECTION HELPERS
# ─────────────────────────────────────────────────────────────────────────────

import re as _re
# Volcano Engine model names end with a 6-digit date suffix, e.g. -251222, -260215.
# This distinguishes Volcano-hosted GLM (glm-4-7-251222) from Zhipu-native GLM (glm-4).
_VOLC_DATE_SUFFIX = _re.compile(r"-\d{6}$")


def _is_volc_hosted(model: str) -> bool:
    """
    Return True if this GLM/seed model is hosted on Volcano Engine (方舟平台)
    rather than the vendor's own API.

    Heuristic: Volcano Engine appends a 6-digit date suffix to every model name
    it hosts, e.g. glm-4-7-251222 or doubao-seed-2-0-pro-260215.
    Zhipu-native models have no such suffix: glm-4, glm-4-flash, glm-z1-flash.
    """
    return bool(_VOLC_DATE_SUFFIX.search(model.lower()))


def _detect_provider(model: str, explicit_provider: str, base_url: str | None) -> str:
    """
    Resolve the effective provider from the model name, explicit flag, and base URL.

    volc / moonshot / zhipu are accepted as explicit aliases — all three route
    to run_openai() with the appropriate base_url.
    """
    # Named aliases are immediately normalised to "openai"
    if explicit_provider in ("volc", "moonshot", "zhipu"):
        return "openai"

    if explicit_provider != "auto":
        return explicit_provider

    m = model.lower()

    if "claude" in m:
        return "anthropic"
    if any(x in m for x in ("gpt", "o1", "o3", "o4", "o5")):
        return "openai"
    if "gemini" in m:
        return "openai"          # Gemini uses OAI-compat endpoint
    if "deepseek" in m:
        return "openai"
    # ── Volcano Engine / Doubao ───────────────────────────────────────────────
    if any(x in m for x in ("doubao", "ark")):
        return "openai"
    if m.startswith("ep-") and len(m) > 10:
        return "openai"          # Volcano Engine deployment endpoint ID
    # ── GLM on Volcano Engine (date-suffix variant) ───────────────────────────
    # Must be checked BEFORE the Zhipu/GLM block below.
    # glm-4-7-251222 → Volcano (ARK key); glm-4 → Zhipu (ZHIPU_API_KEY)
    if any(x in m for x in ("glm", "chatglm")) and _is_volc_hosted(model):
        return "openai"
    # ── Moonshot / Kimi ──────────────────────────────────────────────────────
    if any(x in m for x in ("moonshot", "kimi")):
        return "openai"
    # ── Zhipu / GLM (native API, no date suffix) ─────────────────────────────
    if any(x in m for x in ("glm", "chatglm")):
        return "openai"
    # ── Explicit non-Anthropic base URL ──────────────────────────────────────
    if base_url and "anthropic.com" not in base_url:
        return "openai"

    return "anthropic"           # default


def _detect_base_url(model: str, explicit_base_url: str | None) -> str | None:
    """Return the correct base URL for known providers, or None for default endpoints."""
    if explicit_base_url is not None:
        return explicit_base_url

    m = model.lower()

    if "deepseek" in m:
        return "https://api.deepseek.com"
    if "gemini" in m:
        return "https://generativelanguage.googleapis.com/v1beta/openai/"
    if any(x in m for x in ("doubao", "ark")) or m.startswith("ep-"):
        return "https://ark.cn-beijing.volces.com/api/v3"
    # GLM hosted on Volcano Engine (date suffix) → ARK endpoint
    if any(x in m for x in ("glm", "chatglm")) and _is_volc_hosted(model):
        return "https://ark.cn-beijing.volces.com/api/v3"
    if any(x in m for x in ("moonshot", "kimi")):
        # kimi-k2.x / kimi-k2-* → official platform endpoint (api.moonshot.ai/v1)
        # moonshot-v1-* → legacy CN endpoint (api.moonshot.cn/v1)
        if m.startswith("kimi-"):
            return "https://api.moonshot.ai/v1"
        return "https://api.moonshot.cn/v1"
    # GLM on Zhipu's own API (no date suffix)
    if any(x in m for x in ("glm", "chatglm")):
        return "https://open.bigmodel.cn/api/paas/v4/"

    return None                  # use SDK default (OpenAI or Anthropic)


def _resolve_api_key(model: str, provider: str, explicit_key: str | None) -> str | None:
    """
    Resolve API key: explicit arg → provider-specific env var → generic fallback.
    Returns None only in dry-run (caller must handle).
    """
    if explicit_key:
        return explicit_key

    import os
    m = model.lower()

    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    if "deepseek" in m:
        return os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if "gemini" in m:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if any(x in m for x in ("doubao", "ark")) or m.startswith("ep-"):
        return os.environ.get("ARK_API_KEY") or os.environ.get("VOLC_API_KEY")
    # GLM on Volcano Engine → needs ARK key, not Zhipu key
    if any(x in m for x in ("glm", "chatglm")) and _is_volc_hosted(model):
        return os.environ.get("ARK_API_KEY") or os.environ.get("VOLC_API_KEY")
    if any(x in m for x in ("moonshot", "kimi")):
        return os.environ.get("MOONSHOT_API_KEY")
    # GLM on Zhipu's own API
    if any(x in m for x in ("glm", "chatglm")):
        return os.environ.get("ZHIPU_API_KEY") or os.environ.get("GLM_API_KEY")

    return os.environ.get("OPENAI_API_KEY")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TravelBench evaluation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Two Paris tasks, Haiku — cheapest to test with
  python run_benchmark.py --tasks par_easy_001 par_medium_002 \\
    --model claude-haiku-4-5-20251001 --api-key sk-ant-...

  # Two tasks, GPT-4o-mini
  python run_benchmark.py --tasks par_easy_001 par_medium_002 \\
    --model gpt-4o-mini --provider openai --api-key sk-...

  # Doubao (Volcano Engine / 火山引擎)  — set ARK_API_KEY env var or pass --api-key
  python run_benchmark.py --city london --run-name test_70 \\
    --model doubao-pro-32k --api-key <ark-key>

  # Doubao via deployment endpoint ID
  python run_benchmark.py --city london --run-name test_70 \\
    --model ep-20250503xxxxxx-yyyyy --api-key <ark-key>

  # Kimi K2.6 — official platform (api.moonshot.ai/v1)
  #   Get key at: https://platform.moonshot.ai/console/api-keys
  python run_benchmark.py --city london --run-name test_70 \\
    --model kimi-k2.6 --api-key <moonshot-key>

  # Kimi K2 thinking variant (reasoning model)
  python run_benchmark.py --city london --run-name test_70 \\
    --model kimi-k2-thinking --api-key <moonshot-key>

  # GLM / Zhipu AI (智谱AI)  — set ZHIPU_API_KEY env var or pass --api-key
  python run_benchmark.py --city london --run-name test_70 \\
    --model glm-4 --api-key <zhipu-key>

  # GLM free reasoning tier (good for sanity checks before spending budget)
  python run_benchmark.py --city london --run-name test_70 \\
    --model glm-z1-flash --api-key <zhipu-key>

  # Dry-run (no API, checks pipeline wiring)
  python run_benchmark.py --tasks par_easy_001 par_medium_002 --dry-run

  # Save results JSON
  python run_benchmark.py --tasks par_easy_001 par_medium_002 \\
    --model claude-haiku-4-5-20251001 --api-key ... --output results/
        """
    )
    parser.add_argument("--tasks",    nargs="+", default=None,
                        help="Explicit task IDs to run")
    parser.add_argument("--city",     default=None,
                        help="City name — auto-discovers all tasks under data/cities/{city}/tasks/")
    parser.add_argument("--window",   default=None,
                        help="Window ID — only run tasks from this window (requires --city)")
    parser.add_argument("--run-name", default=None,
                        help="Run name for DB path")
    parser.add_argument("--model",    default="claude-haiku-4-5-20251001",
                        help="Model name")
    parser.add_argument("--provider",
                        choices=["anthropic", "openai", "volc", "moonshot", "zhipu", "auto"],
                        default="auto",
                        help=(
                            "API provider. 'auto' detects from model name. "
                            "'volc' = Volcano Engine/Doubao, "
                            "'moonshot' = Kimi/Moonshot AI, "
                            "'zhipu' = GLM/Zhipu AI. "
                            "All non-Anthropic providers use the OpenAI-compatible path."
                        ))
    parser.add_argument("--api-key",  default=None,
                        help=(
                            "API key. Falls back to env vars: "
                            "ANTHROPIC_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, "
                            "GEMINI_API_KEY, ARK_API_KEY (Doubao), "
                            "MOONSHOT_API_KEY (Kimi), ZHIPU_API_KEY (GLM)"
                        ))
    parser.add_argument("--base-url", default=None,
                        help="Override base URL for OpenAI-compatible APIs")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Run without API calls using stub plans")
    parser.add_argument("--output",   type=Path, default=None,
                        help="Directory to save result JSONs")
    parser.add_argument("--max-calls", type=int, default=30,
                        help="Max tool calls per task (default 30)")
    parser.add_argument("--no-score", action="store_true",
                        help="Skip evaluator (just collect raw outputs)")
    parser.add_argument("--retries", type=int, default=4,
                        help="Max retries on 429/5xx errors (default 4)")
    parser.add_argument("--retry-delay", type=float, default=10.0,
                        help="Base delay in seconds for retry backoff (default 10)")
    parser.add_argument("--inter-task-delay", type=float, default=0.0,
                        help="Seconds to wait between tasks (default 0; "
                             "set to ~3 for OpenRouter free tier)")
    parser.add_argument("--arm", choices=["faulty", "clean_delete", "clean_equalvol"],
                        default="faulty",
                        help="Ablation arm. 'faulty' (default): noisy "
                             "environment as generated. 'clean_delete': heal "
                             "yelp fields to ground truth + DROP incorrect_source "
                             "blog/forum docs. 'clean_equalvol': heal yelp fields "
                             "+ REPLACE incorrect_source doc bodies with "
                             "length-matched neutral filler (holds doc count and "
                             "text volume constant; removes only the lie).")
    parser.add_argument("--clean-environment", action="store_true",
                        help="DEPRECATED alias for --arm clean_delete. Heals "
                             "yelp fields back to ground truth and drops "
                             "incorrect_source blog/forum docs.")
    args = parser.parse_args()

    # ── Resolve provider / base_url / api_key ────────────────────────────────
    provider = _detect_provider(args.model, args.provider, args.base_url)
    base_url = _detect_base_url(args.model, args.base_url)
    # Write back so _run_task closure sees the resolved values
    args.base_url = base_url

    api_key = None
    if not args.dry_run:
        api_key = _resolve_api_key(args.model, provider, args.api_key)
        if not api_key:
            _env_hint = {
                "anthropic": "ANTHROPIC_API_KEY",
                "openai":    "OPENAI_API_KEY",
            }.get(provider, "the appropriate API key env var")
            m = args.model.lower()
            if any(x in m for x in ("doubao", "ark")) or m.startswith("ep-"):
                _env_hint = "ARK_API_KEY or VOLC_API_KEY"
            elif any(x in m for x in ("moonshot", "kimi")):
                _env_hint = "MOONSHOT_API_KEY"
            elif any(x in m for x in ("glm", "chatglm")):
                _env_hint = "ZHIPU_API_KEY or GLM_API_KEY"
            print(f"❌ No API key. Pass --api-key or set {_env_hint}")
            sys.exit(1)

    # ── Validate: need either --tasks or --city ───────────────────────────────
    if not args.tasks and not args.city:
        print("❌ Provide either --tasks or --city")
        sys.exit(1)

    # ── Load tasks ────────────────────────────────────────────────────────────
    tasks = []
    if args.city:
        _city = args.city.lower()
        _tasks_root = ROOT / "data" / "cities" / _city / "tasks"
        if not _tasks_root.exists():
            print(f"❌ Task directory not found: {_tasks_root}")
            sys.exit(1)
        for tf in sorted(_tasks_root.rglob("*.json")):
            if "agent_log" in str(tf):
                continue
            try:
                task = json.loads(tf.read_text())
            except Exception:
                continue
            if args.window and task.get("window_id") != args.window:
                continue
            tasks.append(task)
        print(f"  Discovered {len(tasks)} tasks for {args.city}"
              + (f" (window: {args.window})" if args.window else ""))
    else:
        for tid in args.tasks:
            try:
                task = load_task(tid, city=args.city)
                tasks.append(task)
            except FileNotFoundError:
                print(f"❌ Task not found: {tid}")
                sys.exit(1)

    if not tasks:
        print("❌ No tasks found")
        sys.exit(1)

    # ── Resolve ground-truth DB path ──────────────────────────────────────────
    eval_db_path = None
    if args.run_name:
        from scripts.generation.db import get_city_db_path
        _eval_city = tasks[0].get("city", "").lower()
        eval_db_path = get_city_db_path(_eval_city, run_name=args.run_name)
        if not eval_db_path.exists():
            print(f"⚠  --run-name '{args.run_name}' DB not found at {eval_db_path}")
        else:
            print(f"  Using evaluator DB: {eval_db_path}")

    set_run_name(args.run_name)
    # --clean-environment is a deprecated alias for --arm clean_delete. If it is
    # set while --arm is left at the default, honour the alias; otherwise --arm
    # wins (and we warn if they conflict).
    _arm = args.arm
    if args.clean_environment:
        if args.arm == "faulty":
            _arm = "clean_delete"
        elif args.arm != "clean_delete":
            print(f"⚠  --clean-environment ignored (conflicts with --arm {args.arm})")
    set_arm(_arm)
    if _arm == "clean_delete":
        print("  🧼 ARM=clean_delete — heal yelp fields + DROP incorrect_source docs")
    elif _arm == "clean_equalvol":
        print("  🧼 ARM=clean_equalvol — heal yelp fields + REPLACE incorrect_source "
              "doc bodies with neutral filler (volume held constant)")

    # ── Token budget estimate ─────────────────────────────────────────────────
    budget = estimate_tokens(tasks)
    print_token_budget(budget, args.model)

    if args.dry_run:
        print("⚠  DRY-RUN MODE — no API calls, stub plans used\n")

    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)

    # ── Run tasks ─────────────────────────────────────────────────────────────
    run_ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    t_start     = time.time()

    _inter_delay = args.inter_task_delay
    if _inter_delay == 0.0 and args.base_url and "openrouter.ai" in args.base_url:
        _inter_delay = 3.0

    def _run_task(task):
        _task_days = task.get("days", 1)
        _max_calls = args.max_calls if args.max_calls != 30 else 15 * _task_days
        if args.dry_run:
            return run_dry(task, args.model)
        elif provider == "anthropic":
            return run_anthropic(task, args.model, api_key, max_tool_calls=_max_calls)
        else:
            return run_openai(task, args.model, api_key,
                              base_url=args.base_url, max_tool_calls=_max_calls)

    def _is_retryable(exc):
        s = str(exc)
        return "429" in s or "rate" in s.lower() or "503" in s or "502" in s

    for i, task in enumerate(tasks):
        tid  = task["task_id"]
        diff = task.get("difficulty","?")
        print(f"[{i+1}/{len(tasks)}] Running {tid}  ({diff})  → {args.model}")

        if i > 0 and _inter_delay > 0:
            time.sleep(_inter_delay)

        t0 = time.time()

        raw = None
        last_exc = None
        for _attempt in range(args.retries + 1):
            try:
                raw = _run_task(task)
                break
            except Exception as e:
                last_exc = e
                if _attempt < args.retries and _is_retryable(e):
                    _wait = args.retry_delay * (2 ** _attempt)
                    print(f"  ⏳ Retryable error ({type(e).__name__}) — "
                          f"waiting {_wait:.0f}s then retrying "
                          f"({_attempt+1}/{args.retries})...")
                    time.sleep(_wait)
                else:
                    break

        if raw is None:
            print(f"  ❌ Error running task: {last_exc}")
            _task_days = task.get("days", 1)
            _max_calls = args.max_calls if args.max_calls != 30 else 15 * _task_days
            raw = {
                "task_id": tid, "model": args.model, "provider": provider,
                "tool_call_log": [], "raw_response": "",
                "usage": {"input_tokens":0,"output_tokens":0,"total_tokens":0},
                "error": str(last_exc),
            }

        elapsed_ms = int((time.time() - t0) * 1000)

        raw["parsed_plan"] = parse_plan(raw.get("raw_response",""))

        scored = {}
        if not args.no_score and not raw.get("error"):
            try:
                scored = evaluate(raw, db_path=eval_db_path)
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                scored = {"error": f"{type(e).__name__}: {e}", "traceback": tb}

        raw["scored"]     = scored
        raw["elapsed_ms"] = elapsed_ms
        raw["difficulty"] = diff
        all_results.append(raw)

        print_task_result(task, raw, scored)
        print(f"  ⏱  {elapsed_ms/1000:.1f}s  "
              f"[{raw['usage']['input_tokens']:,}in + "
              f"{raw['usage']['output_tokens']:,}out = "
              f"{raw['usage']['total_tokens']:,} tokens]")

        # Save full transcript
        _transcript = raw.get("transcript", [])
        if _transcript:
            import gzip as _gz
            _model_safe = args.model.replace("/", "-").replace("\\", "-")
            _log_dir = Path("results") / "transcripts" / _model_safe
            _log_dir.mkdir(parents=True, exist_ok=True)
            _c = scored.get("c_score", {})
            _f = scored.get("f_score", {})
            _p = scored.get("p_score", {})
            _b = scored.get("b_score", {})
            _usage = raw.get("usage", {})
            _log_path = _log_dir / f"{tid}_{run_ts}.json.gz"
            _payload = json.dumps({
                "task_id": tid,
                "model": raw.get("model", "?"),
                "run_ts": run_ts,
                "calls_made": raw.get("calls_made", "?"),
                "think_log": raw.get("think_log", []),
                "elapsed_s": round(elapsed_ms / 1000, 1),
                "input_tokens": _usage.get("input_tokens", 0),
                "output_tokens": _usage.get("output_tokens", 0),
                "total_tokens": _usage.get("total_tokens", 0),
                "c_score": _c.get("score") if isinstance(_c, dict) else None,
                "c_issues": _c.get("deductions", []) if isinstance(_c, dict) else [],
                "c_warnings": _c.get("warnings", []) if isinstance(_c, dict) else [],
                "f_score": _f.get("score") if isinstance(_f, dict) else None,
                "f_deductions": _f.get("deductions", []) if isinstance(_f, dict) else [],
                "p_score": _p.get("score") if isinstance(_p, dict) else None,
                "p_results": _p.get("code_results", []) if isinstance(_p, dict) else [],
                "b_score": _b.get("score") if isinstance(_b, dict) else None,
                "parsed_plan": raw.get("parsed_plan"),
                "transcript": _transcript,
            }, indent=2, ensure_ascii=False).encode()
            _log_path.write_bytes(_gz.compress(_payload))
            print(f"  📝 Transcript: {_log_path.name} ({len(_gz.compress(_payload))//1024}KB)")

        # Save scores to SQLite
        try:
            import sqlite3 as _sq
            _db_dir = Path("results")
            _db_dir.mkdir(parents=True, exist_ok=True)
            _sconn = _sq.connect(_db_dir / "scores.db")
            _sconn.execute("""
                CREATE TABLE IF NOT EXISTS scores (
                    run_ts      TEXT,
                    task_id     TEXT,
                    model       TEXT,
                    difficulty  TEXT,
                    calls_made  INTEGER,
                    elapsed_s   REAL,
                    input_tokens  INTEGER,
                    output_tokens INTEGER,
                    c_score     REAL,
                    f_score     REAL,
                    p_score     REAL,
                    b_score     REAL,
                    c_issues    INTEGER,
                    c_warnings  INTEGER,
                    f_deductions INTEGER,
                    PRIMARY KEY (run_ts, task_id, model)
                )
            """)
            _c2 = scored.get("c_score", {})
            _f2 = scored.get("f_score", {})
            _p2 = scored.get("p_score", {})
            _b2 = scored.get("b_score", {})
            _usage2 = raw.get("usage", {})
            _sconn.execute("""
                INSERT OR REPLACE INTO scores VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run_ts, tid, args.model, diff,
                raw.get("calls_made", 0),
                round(elapsed_ms / 1000, 1),
                _usage2.get("input_tokens", 0),
                _usage2.get("output_tokens", 0),
                _c2.get("score") if isinstance(_c2, dict) else None,
                _f2.get("score") if isinstance(_f2, dict) else None,
                _p2.get("score") if isinstance(_p2, dict) else None,
                _b2.get("score") if isinstance(_b2, dict) else None,
                len(_c2.get("deductions", [])) if isinstance(_c2, dict) else 0,
                len(_c2.get("warnings", [])) if isinstance(_c2, dict) else 0,
                len(_f2.get("deductions", [])) if isinstance(_f2, dict) else 0,
            ))
            _sconn.commit()
            _sconn.close()
        except Exception as _e:
            print(f"  ⚠ Score DB write failed: {_e}")

        if args.output:
            result_path = args.output / f"{run_ts}_{tid}_{args.model.replace('/','-')}.json"
            result_path.write_text(json.dumps(raw, indent=2))
            print(f"  💾 Saved: {result_path.name}")

    total_ms = int((time.time() - t_start) * 1000)
    print_run_summary(all_results, args.model, total_ms)

    if args.output:
        summary_path = args.output / f"{run_ts}_summary_{args.model.replace('/','-')}.json"
        summary_path.write_text(json.dumps({
            "run_ts":   run_ts,
            "model":    args.model,
            "provider": provider,
            "tasks":    [r["task_id"] for r in all_results],
            "results":  all_results,
            "total_tokens": sum(r["usage"]["total_tokens"] for r in all_results),
            "total_ms": total_ms,
        }, indent=2))
        print(f"\n💾 Summary saved: {summary_path.name}")


if __name__ == "__main__":
    main()
