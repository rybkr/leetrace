#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

python3 "$PROJECT_ROOT/problem_repair_scripts/repair_problems_with_codex.py" \
  --exclude-run-dir "$PROJECT_ROOT/codex_problem_repair_runs/smoke_single_easy_v2" \
  --exclude-run-dir "$PROJECT_ROOT/codex_problem_repair_runs/pilot_20_each_v1" \
  --exclude-run-dir "$PROJECT_ROOT/codex_problem_repair_campaigns/full_run_gpt54_campaign/round_01" \
  --include-id-file "$PROJECT_ROOT/codex_problem_repair_campaigns/full_run_gpt54_campaign/round_01/remaining_medium_and_holdout_ids.txt" \
  --difficulty Medium \
  --difficulty Easy \
  --model gpt-5.4 \
  --reasoning-effort high \
  --batch-size 20 \
  --parallelism 3 \
  --worker-timeout-seconds 1800 \
  "$@"
