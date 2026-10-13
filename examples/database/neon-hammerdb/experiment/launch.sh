#!/usr/bin/env bash
# Launch the neon-hammerdb experiment (expects an interactive TTY, e.g. a tmux pane).
# Refuses to start a second run if the collection already has one; use resume.sh then.
set -euo pipefail
source "$(dirname "$0")/experiment.env.sh"
load_credentials

if [ -d "$RUNS_DIR" ] && [ -n "$(ls "$RUNS_DIR" 2>/dev/null)" ]; then
  echo "error: $RUNS_DIR already contains a project; use resume.sh" >&2
  exit 1
fi

# Build the HammerDB toolchain once up front so round 1 does not pay for it.
bash "$VIBESYS_DIR/$INPUT/benchmark/setup_tools.sh" "${NEON_HAMMERDB_TOOLS:-$HOME/.cache/vibesys-neon-hammerdb/tools}" >/dev/null

cd "$VIBESYS_DIR"
uv run vibesys \
  "${VIBESYS_COMMON_FLAGS[@]}" \
  --input "$INPUT" \
  --max-rounds "$MAX_ROUNDS"
