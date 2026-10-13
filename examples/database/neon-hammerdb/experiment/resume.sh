#!/usr/bin/env bash
# Resume the neon-hammerdb experiment after a quit or TUI restart. Repeats the constraints
# verbatim (required). Raise MAX_ROUNDS in experiment.env.sh to extend a finished run.
set -euo pipefail
source "$(dirname "$0")/experiment.env.sh"
load_credentials

PROJECT="$(ls "$RUNS_DIR" 2>/dev/null | head -1)"
[ -n "$PROJECT" ] || { echo "error: no project in $RUNS_DIR; launch first" >&2; exit 1; }

cd "$VIBESYS_DIR"
uv run vibesys \
  "${VIBESYS_COMMON_FLAGS[@]}" \
  --resume "$PROJECT" \
  --max-rounds "$MAX_ROUNDS"
