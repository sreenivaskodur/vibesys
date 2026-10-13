#!/usr/bin/env bash
# One A/B experiment run on a fresh branch of the golden data set.
# Usage: GOLDEN_DIR=... run_exp.sh <label> <neon_root> [tpcc|tpch|both|tpchq]
#   tpchq: time only the TPC-H queries in QUERIES (default "6 14 3 1") via psql, for fast iteration.
#   VUS=16 RAMPUP=1 DURATION=3; EXTRA_CONF adds compute settings; NEON_* env reaches the services.
set -uo pipefail
LABEL=$1; NR=$2; WHAT=${3:-both}
export NEON_ROOT=$NR
source "$(dirname "$0")/../env.sh"
export NEON_REPO_DIR=${GOLDEN_DIR:?set GOLDEN_DIR}
export PGPORT=55432 PGSU=cloud_admin WAREHOUSES=${WAREHOUSES:-50} SF=${SF:-10}
export RAMPUP=${RAMPUP:-1} DURATION=${DURATION:-3} VUS=${VUS:-16}
export PGBIN=$NR/pg_install/v17/bin POSTGRES_DISTRIB_DIR=$NR/pg_install NEON_BIN=$NR/target/release
NL=$NR/target/release/neon_local
# Point the golden cluster at this build tree.
sed -i '' -e "s|^pg_distrib_dir = .*|pg_distrib_dir = \"$NR/pg_install\"|" \
          -e "s|^neon_distrib_dir = .*|neon_distrib_dir = \"$NR/target/release\"|" "$NEON_REPO_DIR/config"
OUT=$RESULTS_DIR/exp/$(date +%m%d-%H%M%S)-$LABEL; mkdir -p "$OUT"
cd "$NR"
$NL start > "$OUT/start.log" 2>&1 || { echo "cluster start failed"; cat "$OUT/start.log"; exit 1; }
BR=b$(date +%H%M%S)
cleanup() { $NL endpoint stop "$BR" --destroy >/dev/null 2>&1; $NL stop >/dev/null 2>&1; }
trap cleanup EXIT
$NL timeline branch --branch-name "$BR" --ancestor-branch-name main >/dev/null
$NL endpoint create "$BR" --branch-name "$BR" --pg-version 17 --pg-port $PGPORT >/dev/null
configure_endpoint "$NEON_REPO_DIR/endpoints/$BR"
$NL endpoint start "$BR" > "$OUT/endpoint.log" 2>&1 || { echo "endpoint start failed"; tail "$OUT/endpoint.log"; exit 1; }
{ echo "label=$LABEL root=$NR vus=$VUS rampup=$RAMPUP duration=$DURATION what=$WHAT"
  git -C "$NR" log -1 --format='%h %s' 2>/dev/null
  echo "EXTRA_CONF=${EXTRA_CONF:-}"; env | grep '^NEON_SAFEKEEPER\|^NEON_PAGESERVER' || true
  "$PGBIN/psql" -h 127.0.0.1 -p $PGPORT -U $PGSU -d postgres -Atc "show effective_io_concurrency; show neon.file_cache_size_limit"
} > "$OUT/meta.txt" 2>&1
if [ "$WHAT" = tpcc ] || [ "$WHAT" = both ]; then
  "$TOOLS_DIR/snapshot.sh" neon "$OUT/tpcc_before"
  XT=$(xtprof_log); rm -f "$XT"; hdb tpcc_run.tcl > "$OUT/tpcc_run.log"; cp "$XT" "$OUT/tpcc_latency.log" 2>/dev/null
  "$TOOLS_DIR/snapshot.sh" neon "$OUT/tpcc_after"
  grep 'TEST RESULT' "$OUT/tpcc_run.log" | sed "s/^/[$LABEL] /"
  sed -n '/SUMMARY OF/,$p' "$OUT/tpcc_latency.log" | grep -E 'PROC: NEWORD' -A2 | grep P99 | sed "s/^/[$LABEL] NEWORD /"
fi
if [ "$WHAT" = tpchq ]; then
  "$TOOLS_DIR/snapshot.sh" neon "$OUT/tpchq_before"
  "$(dirname "$0")/tpch_queries.sh" "$OUT/tpchq.txt" ${QUERIES:-6 14 3 1} | sed "s/^/[$LABEL] /"
  "$TOOLS_DIR/snapshot.sh" neon "$OUT/tpchq_after"
elif [ "$WHAT" != tpcc ]; then
  "$TOOLS_DIR/snapshot.sh" neon "$OUT/tpch_before"
  hdb tpch_run.tcl > "$OUT/tpch_run.log"
  "$TOOLS_DIR/snapshot.sh" neon "$OUT/tpch_after"
  grep -E 'Completed|Geometric' "$OUT/tpch_run.log" | sed "s/^/[$LABEL] /"
fi
echo "$OUT"
