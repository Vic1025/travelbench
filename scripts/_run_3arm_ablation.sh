#!/bin/bash
# 3-arm ablation runner — runs the SAME city/window/tasks for one model under
# all three arms so the only variable that differs across runs is the
# wrong-info treatment:
#
#   faulty          noisy environment, as generated (incorrect_source docs
#                   present, yelp carries wrong values)
#   clean_delete    heal yelp fields to ground truth + DROP incorrect_source docs
#                   (confounds "trap removed" with "less context")
#   clean_equalvol  heal yelp fields + REPLACE incorrect_source doc bodies with
#                   length-matched neutral filler (doc count + text volume held
#                   ≈ constant; the ONLY removed variable is the lie)
#
# This script only orchestrates run_benchmark invocations. It does NOT run any
# analysis itself. Each arm produces a separate set of scores rows.
#
# Usage:
#   scripts/_run_3arm_ablation.sh <model> <city> [window_id] [run_name] [max_calls]
#
# Examples:
#   scripts/_run_3arm_ablation.sh deepseek-chat new_york
#   scripts/_run_3arm_ablation.sh gemini-3.1-pro-preview new_york win_summer test_70 30
#
# NOTE: this makes real API calls (one full pass per arm). Do not run unless you
#       intend to spend the budget.

set -u
cd /Users/victoriajin/Documents/Career_related/TravelPlanningAgent/travelbench_Phase5.10

MODEL="${1:?usage: $0 <model> <city> [window] [run_name] [max_calls]}"
CITY="${2:?usage: $0 <model> <city> [window] [run_name] [max_calls]}"
WINDOW="${3:-}"
RUN_NAME="${4:-}"
MAX_CALLS="${5:-30}"

ARMS=(faulty clean_delete clean_equalvol)

STAMP=$(date +%Y%m%d_%H%M%S)
LOGDIR="results/ablation_3arm/${MODEL//\//_}_${CITY}_${STAMP}"
mkdir -p "$LOGDIR"

echo "===== 3-ARM ABLATION STARTED $(date) =====" | tee "$LOGDIR/_master.log"
echo "model=$MODEL city=$CITY window=${WINDOW:-<all>} run_name=${RUN_NAME:-<none>} max_calls=$MAX_CALLS" \
  | tee -a "$LOGDIR/_master.log"
echo "arms: ${ARMS[*]}" | tee -a "$LOGDIR/_master.log"

WINDOW_ARG=()
[ -n "$WINDOW" ]   && WINDOW_ARG=(--window "$WINDOW")
RUN_NAME_ARG=()
[ -n "$RUN_NAME" ] && RUN_NAME_ARG=(--run-name "$RUN_NAME")

for ARM in "${ARMS[@]}"; do
  echo "" | tee -a "$LOGDIR/_master.log"
  echo "===== ${MODEL} · arm=${ARM} · $(date) =====" | tee -a "$LOGDIR/_master.log"
  LOG="$LOGDIR/${ARM}.log"
  /usr/bin/time -p python3 run_benchmark.py \
      --city "$CITY" \
      "${WINDOW_ARG[@]}" \
      --model "$MODEL" \
      --max-calls "$MAX_CALLS" \
      --retries 0 \
      "${RUN_NAME_ARG[@]}" \
      --arm "$ARM" \
      > "$LOG" 2>&1
  RC=$?
  echo "  exit_code=$RC · log=$LOG" | tee -a "$LOGDIR/_master.log"
done

echo "" | tee -a "$LOGDIR/_master.log"
echo "===== 3-ARM ABLATION COMPLETE $(date) =====" | tee -a "$LOGDIR/_master.log"
echo "Compare arms with: results/scores.db (filter by run_ts >= $STAMP and arm)" \
  | tee -a "$LOGDIR/_master.log"
