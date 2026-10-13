# Shared settings for the manual tools (sourced). Required: NEON_ROOT, a built neon checkout.
#   NEON_ROOT       neon tree with target/release and pg_install (make BUILD_TYPE=release)
#   HAMMERDB_TOOLS  output of ../benchmark/setup_tools.sh (default ~/.cache/vibesys-neon-hammerdb/tools)
#   RESULTS_DIR     where runs, snapshots and reports go (default ./neon-bench-results)
EXAMPLE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
export TPCDIR=$EXAMPLE_DIR/benchmark/hammerdb TOOLS_DIR=$EXAMPLE_DIR/tools
: "${NEON_ROOT:?set NEON_ROOT to a built neon checkout}"
export NEON_ROOT
export HAMMERDB_TOOLS=${HAMMERDB_TOOLS:-$HOME/.cache/vibesys-neon-hammerdb/tools}
export HAMMERDB_DIR=$HAMMERDB_TOOLS/HammerDB
export TCLLIBPATH="$HAMMERDB_TOOLS/lib $HAMMERDB_TOOLS/lib/ticklecharts"
export RESULTS_DIR=${RESULTS_DIR:-$PWD/neon-bench-results}
mkdir -p "$RESULTS_DIR"
# Compute settings: keep identical to benchmark/harness.py COMPUTE_SETTINGS.
COMPUTE_CONF="shared_buffers=2GB
max_connections=200
max_wal_size=8GB
checkpoint_timeout=15min
work_mem=64MB
maintenance_work_mem=1GB
max_parallel_workers_per_gather=8
max_parallel_workers=16
max_worker_processes=32"
LFC_SIZE=${LFC_SIZE:-8GB}
hdb() { (cd "$HAMMERDB_DIR" && ./hammerdbcli auto "$TPCDIR/$1" 2>&1); }
xtprof_log() { echo "$(getconf DARWIN_USER_TEMP_DIR)hdbxtprofile.log"; }
# Append the fixed compute settings + LFC to a neon_local endpoint's config.
configure_endpoint() {  # $1 = endpoint dir
  echo "$COMPUTE_CONF" >> "$1/postgresql.conf"
  mkdir -p "$1/file_cache"
  printf "neon.file_cache_path='%s'\nneon.max_file_cache_size=%s\nneon.file_cache_size_limit=%s\n" \
    "$1/file_cache/file.cache" "$LFC_SIZE" "$LFC_SIZE" >> "$1/postgresql.conf"
  if [ -n "${EXTRA_CONF:-}" ]; then echo "$EXTRA_CONF" >> "$1/postgresql.conf"; fi
}
