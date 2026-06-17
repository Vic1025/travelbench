#!/bin/bash
# Slide-11 pilot — clean (--clean-environment) vs faulty (default).
# Uses GEMINI + DEEPSEEK keys only to avoid colliding with the leaderboard
# sweep (which uses ARK + MOONSHOT + OPENAI + ANTHROPIC).

set -u
cd /Users/victoriajin/Documents/Career_related/TravelPlanningAgent/travelbench_Phase5.10

TASKS=(
  claude_sonnet_4_5_new_york_type1_20260521_1779375385
  gemini_3_1_pro_preview_new_york_type2_20260521_1779375520
  gemini_3_1_pro_preview_new_york_type3_20260521_1779375659
  gpt_5_4_new_york_type4_20260521_1779375371
  gemini_3_1_pro_preview_new_york_type5_20260521_1779375933
)
# Dropping type6 (waterfront/rooftop exclusions = small solution pool, unlikely
# to touch wrong-info venues meaningfully).

MODELS=(
  deepseek-chat                # DEEPSEEK key
  gemini-3.1-pro-preview       # GEMINI key
)

STAMP=$(date +%Y%m%d_%H%M%S)
LOGDIR=results/pilot_clean_vs_faulty/${STAMP}
mkdir -p "$LOGDIR"

echo "===== PILOT STARTED $(date) =====" | tee "$LOGDIR/_master.log"
echo "Tasks: ${#TASKS[@]}  ·  Models: ${#MODELS[@]}  ·  2 modes = $(( ${#TASKS[@]} * ${#MODELS[@]} * 2 )) runs" | tee -a "$LOGDIR/_master.log"

for MODEL in "${MODELS[@]}"; do
  for MODE in faulty clean; do
    FLAG=""
    [ "$MODE" = "clean" ] && FLAG="--clean-environment"
    RUN_NAME="test_70_${MODE}"

    echo "" | tee -a "$LOGDIR/_master.log"
    echo "===== ${MODEL}  ·  ${MODE}  ·  $(date) =====" | tee -a "$LOGDIR/_master.log"
    LOG="$LOGDIR/${MODEL//\//_}_${MODE}.log"
    /usr/bin/time -p python3 run_benchmark.py \
        --tasks "${TASKS[@]}" \
        --model "$MODEL" \
        --max-calls 30 \
        --retries 0 \
        --run-name "$RUN_NAME" \
        $FLAG \
        > "$LOG" 2>&1
    RC=$?
    LAST_TS=$(sqlite3 results/scores.db "SELECT MAX(run_ts) FROM scores WHERE model='$MODEL';" 2>/dev/null)
    N_NEW=$(sqlite3 results/scores.db "SELECT COUNT(*) FROM scores WHERE model='$MODEL' AND run_ts='$LAST_TS';" 2>/dev/null)
    echo "  exit_code=$RC · latest_run_ts=$LAST_TS · rows_added=$N_NEW" | tee -a "$LOGDIR/_master.log"
  done
done

echo "" | tee -a "$LOGDIR/_master.log"
echo "===== PILOT COMPLETE $(date) =====" | tee -a "$LOGDIR/_master.log"

# Per-task delta summary (clean − faulty)
echo "" | tee -a "$LOGDIR/_master.log"
echo "PER-MODEL means — clean vs faulty (only rows since $STAMP):" | tee -a "$LOGDIR/_master.log"
sqlite3 -column -header results/scores.db "
SELECT
  model,
  CASE WHEN run_ts >= '$STAMP' AND EXISTS (
         SELECT 1 FROM scores s2 WHERE s2.run_ts = s.run_ts AND s2.task_id = s.task_id
       ) THEN 'present' ELSE 'absent' END AS rn,
  COUNT(*) AS n,
  ROUND(AVG(c_score),3) AS mean_c,
  ROUND(AVG(f_score),3) AS mean_f,
  ROUND(AVG(p_score),3) AS mean_p
FROM scores s
WHERE model IN ('${MODELS[0]}','${MODELS[1]}')
  AND run_ts >= '$STAMP'
GROUP BY model, run_ts
ORDER BY model, run_ts;
" | tee -a "$LOGDIR/_master.log"
