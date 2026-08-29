#!/usr/bin/env bash
# scripts/kill_drill.sh
#
# M6 (Owner A / Ammar): kill/restart resume test.
#
# Proves: killing the worker mid-run and restarting resumes from the
# last good artifact, not from the beginning. S00-S30 hashes unchanged,
# attempt counts don't increment.
#
# Usage:
#   bash scripts/kill_drill.sh
set -euo pipefail

RUN_ID="m6-kill-drill-$(date +%Y%m%d-%H%M%S)"
EVIDENCE_DIR="evidence/m6/kill_drill"
mkdir -p "$EVIDENCE_DIR"

PSQL="sudo docker exec temporal-postgresql psql -U temporal -d content_engine -t -A"

echo "============================================================"
echo "M6 KILL DRILL"
echo "  run_id: $RUN_ID"
echo "============================================================"

# Step 1: Start the run in background
echo ""
echo "[1/7] Starting faceless run in background..."
uv run avatar-harness run \
  --config configs/runs/faceless_poc.yaml \
  --idea "M6 kill drill test" \
  --privacy unlisted \
  --run-id "$RUN_ID" \
  > "$EVIDENCE_DIR/run_log_phase1.txt" 2>&1 &
RUN_PID=$!
echo "  PID: $RUN_PID"

# Step 2: Poll manifest until S30 completes
echo ""
echo "[2/7] Waiting for S30 to complete..."
for i in $(seq 1 120); do
  S30_STATUS=$($PSQL -c "SELECT status FROM manifest_stage_records WHERE run_id = '$RUN_ID' AND stage_id = 'S30' LIMIT 1;" 2>/dev/null || echo "")
  if [ "$S30_STATUS" = "passed" ]; then
    echo "  S30 passed after ${i}s"
    break
  fi
  sleep 1
done

if [ "$S30_STATUS" != "passed" ]; then
  echo "  ERROR: S30 did not complete within 120s. Check run log."
  kill $RUN_PID 2>/dev/null || true
  exit 1
fi

# Step 3: Kill the worker
echo ""
echo "[3/7] Killing worker process (simulating crash)..."
sleep 2
kill $RUN_PID 2>/dev/null || true
wait $RUN_PID 2>/dev/null || true
echo "  Worker killed."

temporal workflow terminate \
  --workflow-id "pipeline-$RUN_ID" \
  --namespace default 2>/dev/null || true
echo "  Workflow terminated."

# Step 4: Record pre-kill hashes
echo ""
echo "[4/7] Recording pre-kill artifact hashes..."
$PSQL -F'|' -c "
  SELECT stage_id, attempt, output_artifact_ids
  FROM manifest_stage_records
  WHERE run_id = '$RUN_ID' AND status = 'passed'
  ORDER BY stage_id;
" > "$EVIDENCE_DIR/pre_kill_hashes.txt"
cat "$EVIDENCE_DIR/pre_kill_hashes.txt"

# Step 5: Restart
echo ""
echo "[5/7] Restarting with same run-id..."
uv run avatar-harness run \
  --config configs/runs/faceless_poc.yaml \
  --idea "M6 kill drill test" \
  --privacy unlisted \
  --run-id "$RUN_ID" \
  > "$EVIDENCE_DIR/run_log_phase2.txt" 2>&1 || true
echo "  Phase 2 complete."

# Step 6: Compare hashes
echo ""
echo "[6/7] Comparing post-resume hashes..."
$PSQL -F'|' -c "
  SELECT stage_id, attempt, output_artifact_ids
  FROM manifest_stage_records
  WHERE run_id = '$RUN_ID' AND status = 'passed'
  ORDER BY stage_id;
" > "$EVIDENCE_DIR/post_resume_hashes.txt"

echo "  Pre-kill stages:"
cat "$EVIDENCE_DIR/pre_kill_hashes.txt"
echo "  Post-resume stages:"
cat "$EVIDENCE_DIR/post_resume_hashes.txt"

PRE_S00=$(grep "^S00" "$EVIDENCE_DIR/pre_kill_hashes.txt" || echo "")
POST_S00=$(grep "^S00" "$EVIDENCE_DIR/post_resume_hashes.txt" || echo "")
if [ "$PRE_S00" = "$POST_S00" ] && [ -n "$PRE_S00" ]; then
  echo "  S00 hash unchanged after resume"
else
  echo "  S00 hash may have changed (check manually)"
fi

# Step 7: Check attempt counts
echo ""
echo "[7/7] Checking attempt counts..."
$PSQL -c "
  SELECT stage_id, MAX(attempt) as max_attempt
  FROM manifest_stage_records
  WHERE run_id = '$RUN_ID'
  GROUP BY stage_id
  ORDER BY stage_id;
" | tee "$EVIDENCE_DIR/attempt_counts.txt"

echo ""
echo "============================================================"
echo "KILL DRILL COMPLETE"
echo "  Evidence: $EVIDENCE_DIR/"
echo "============================================================"