#!/bin/bash
# Leaderboard extension sweep — May–June 2026 NYC test_70
# Adds: doubao, glm, kimi (cheap newcomers) + gpt-5.5, claude-opus-4-8 (frontier)
# Runs against the same 6 tasks already covered for the 4-model panel.
# Uses --retries 0 per memory/feedback_real_billing_vs_local_accounting.

set -u  # error on undefined vars; do NOT set -e — keep going on per-model failure
cd /Users/victoriajin/Documents/Career_related/TravelPlanningAgent/travelbench_Phase5.10

TASKS=(
  claude_sonnet_4_5_new_york_type1_20260521_1779375385
  gemini_3_1_pro_preview_new_york_type2_20260521_1779375520
  gemini_3_1_pro_preview_new_york_type3_20260521_1779375659
  gpt_5_4_new_york_type4_20260521_1779375371
  gemini_3_1_pro_preview_new_york_type5_20260521_1779375933
  gpt_5_4_new_york_type6_20260521_1779375462
)

# Models: cheap first (so the expensive ones land last and we can abort early if needed)
MODELS=(
  doubao-seed-2-0-pro-260215
  glm-4-7-251222
  kimi-k2.6
  gpt-5.5
  claude-opus-4-8
)

STAMP=$(date +%Y%m%d_%H%M%S)
LOGDIR=results/sweep_extension/${STAMP}
mkdir -p "$LOGDIR"

echo "===== SWEEP STARTED $(date) =====" | tee "$LOGDIR/_master.log"
echo "Tasks: ${#TASKS[@]}  ·  Models: ${#MODELS[@]}  ·  Log dir: $LOGDIR" | tee -a "$LOGDIR/_master.log"

for MODEL in "${MODELS[@]}"; do
  echo "" | tee -a "$LOGDIR/_master.log"
  echo "===== MODEL: $MODEL  ·  $(date) =====" | tee -a "$LOGDIR/_master.log"
  LOG="$LOGDIR/${MODEL//\//_}.log"
  /usr/bin/time -p python3 run_benchmark.py \
      --tasks "${TASKS[@]}" \
      --model "$MODEL" \
      --max-calls 30 \
      --retries 0 \
      --run-name test_70 \
      > "$LOG" 2>&1
  RC=$?
  LAST_TS=$(sqlite3 results/scores.db "SELECT MAX(run_ts) FROM scores WHERE model='$MODEL';" 2>/dev/null)
  N_NEW=$(sqlite3 results/scores.db "SELECT COUNT(*) FROM scores WHERE model='$MODEL' AND run_ts='$LAST_TS';" 2>/dev/null)
  echo "  exit_code=$RC · latest_run_ts=$LAST_TS · rows_added=$N_NEW" | tee -a "$LOGDIR/_master.log"
done

echo "" | tee -a "$LOGDIR/_master.log"
echo "===== SWEEP COMPLETE $(date) =====" | tee -a "$LOGDIR/_master.log"
echo "Log dir: $LOGDIR" | tee -a "$LOGDIR/_master.log"

# Summary table of scores added
sqlite3 -column -header results/scores.db "
SELECT model, COUNT(*) AS rows, ROUND(AVG(c_score),3) AS mean_c, ROUND(AVG(f_score),3) AS mean_f, ROUND(AVG(p_score),3) AS mean_p
FROM scores
WHERE model IN ('${MODELS[0]}','${MODELS[1]}','${MODELS[2]}','${MODELS[3]}','${MODELS[4]}')
  AND run_ts >= '$STAMP'
GROUP BY model;
" | tee -a "$LOGDIR/_master.log"
