#!/usr/bin/env bash
# Shared definition of the neon-hammerdb experiment. Sourced by launch.sh and resume.sh so
# the constraints repeat byte-identically on resume (vibesys requires repeated --constraint
# flags to match exactly).

VIBESYS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
RUNS_DIR="$HOME/vibesys-runs/neon-hammerdb"
INPUT="examples/database/neon-hammerdb"
MAX_ROUNDS=10

CONSTRAINT_MACHINE="Optimize for this specific host (macOS on Apple Silicon, one internal NVMe SSD): machine-specific code paths and defaults are allowed when they keep Neon's durability and correctness guarantees. Attribute every claimed gain with an A/B measurement against the previous checkpoint (tools/exp/run_exp.sh) and a profile or counter snapshot."
CONSTRAINT_DURABILITY="The safekeeper must make WAL durable with the same full drive-cache flush as unmodified Neon (F_FULLFSYNC on macOS) before a commit is acknowledged. Gains that come from weakening durability, correctness, or the benchmark's fixed settings do not count."

VIBESYS_COMMON_FLAGS=(
  --runs-dir "$RUNS_DIR"
  --local
  --backend cpu
  --profiler none
  --constraint "$CONSTRAINT_MACHINE"
  --constraint "$CONSTRAINT_DURABILITY"
)

# Agents run under the macOS Seatbelt sandbox, which denies SysV IPC; PostgreSQL on macOS
# needs SysV semaphores, so agents cannot start a cluster to test locally while it is on.
# The trusted benchmark and accuracy checker are unaffected (they run on the host).
# Set to 0 to let agents run Postgres themselves (removes the agent sandbox entirely).
export VIBESYS_AGENT_SANDBOX="${VIBESYS_AGENT_SANDBOX:-1}"

load_credentials() {
  if [ -f "$HOME/.config/anthropic/env" ]; then
    set -a; source "$HOME/.config/anthropic/env"; set +a
  fi
}
