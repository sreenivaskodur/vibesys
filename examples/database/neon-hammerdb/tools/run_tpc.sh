#!/usr/bin/env bash
# Full comparison run: TPROC-C at several VU counts and TPROC-H, on vanilla PG17 or local Neon,
# with counter snapshots, latency profiles and samply/pageserver CPU captures.
# Usage: NEON_ROOT=... run_tpc.sh vanilla|neon
#   WAREHOUSES=50 SF=10 RAMPUP=1 DURATION=5 VULIST="8 16 32" PROFILE_VU=16 PROFILE_SECS=30 RESULTS_TAG=
set -uo pipefail
source "$(dirname "$0")/env.sh"
export WAREHOUSES=${WAREHOUSES:-50} SF=${SF:-10} RAMPUP=${RAMPUP:-1} DURATION=${DURATION:-5}
VULIST=${VULIST:-"8 16 32"}
export PGBIN=$NEON_ROOT/pg_install/v17/bin PROFILE_VU=${PROFILE_VU:-16} PROFILE_SECS=${PROFILE_SECS:-30}
TARGET=$1; OUT=$RESULTS_DIR/${RESULTS_TAG:-}$TARGET; mkdir -p "$OUT"

if [ "$TARGET" = vanilla ]; then
  export PGPORT=5499 PGSU=postgres
  D=$RESULTS_DIR/vanilla_data; rm -rf "$D"
  "$PGBIN/initdb" -D "$D" -U postgres --auth=trust >/dev/null
  echo "$COMPUTE_CONF" >> "$D/postgresql.conf"
  "$PGBIN/pg_ctl" -D "$D" -o "-p $PGPORT" -l "$OUT/postgres.log" -w start
  stop() { "$PGBIN/pg_ctl" -D "$D" -m fast -w stop; }
else
  export PGPORT=55432 PGSU=cloud_admin
  export NEON_REPO_DIR=$RESULTS_DIR/neon_repo; rm -rf "$NEON_REPO_DIR"
  NL="$NEON_ROOT/target/release/neon_local"
  cd "$NEON_ROOT"
  export POSTGRES_DISTRIB_DIR=$NEON_ROOT/pg_install NEON_BIN=$NEON_ROOT/target/release
  $NL init >/dev/null
  $NL start
  $NL tenant create --set-default --pg-version 17
  $NL endpoint create main --pg-version 17 --pg-port $PGPORT
  configure_endpoint "$NEON_REPO_DIR/endpoints/main"
  $NL endpoint start main
  stop() { cd "$NEON_ROOT"; $NL endpoint stop main; $NL stop; }
fi
trap stop EXIT
PSQL() { "$PGBIN/psql" -h 127.0.0.1 -p $PGPORT -U $PGSU -d postgres -Atc "$1"; }
PSQL "select version(); show shared_buffers;" | tee "$OUT/server.txt"
if [ "$TARGET" = neon ]; then PSQL "show neon.file_cache_path; show neon.max_file_cache_size; show neon.file_cache_size_limit;" | tee -a "$OUT/server.txt"; fi

echo "== TPROC-C build ($WAREHOUSES warehouses)"
/usr/bin/time -p bash -c "source '$TOOLS_DIR/env.sh'; hdb tpcc_build.tcl" > "$OUT/tpcc_build.log" 2>&1
if grep -qE "^Error|FAILED" "$OUT/tpcc_build.log"; then echo "TPC-C build failed"; exit 1; fi
for v in $VULIST; do
  echo "== TPROC-C run VU=$v"
  "$TOOLS_DIR/snapshot.sh" "$TARGET" "$OUT/tpcc_vu${v}_before"
  CAP=""
  if [ "$v" = "$PROFILE_VU" ]; then
    "$TOOLS_DIR/capture.sh" "$TARGET" "$OUT/profile_tpcc_vu$v" $((RAMPUP*60+60)) "$PROFILE_SECS" tpcc & CAP=$!
  fi
  XT=$(xtprof_log); rm -f "$XT"
  VUS=$v hdb tpcc_run.tcl > "$OUT/tpcc_run_vu$v.log"
  cp "$XT" "$OUT/tpcc_latency_vu$v.log" 2>/dev/null && sed -n '/SUMMARY OF/,$p' "$OUT/tpcc_latency_vu$v.log" \
    | grep -E 'PROC|P99' | paste - - | awk '{printf "    %-8s p50=%s p95=%s p99=%s\n", $3, $11, $7, $5}'
  if [ -n "$CAP" ]; then wait $CAP; fi
  "$TOOLS_DIR/snapshot.sh" "$TARGET" "$OUT/tpcc_vu${v}_after"
  grep -E 'TEST RESULT' "$OUT/tpcc_run_vu$v.log" | tail -1
done
echo "== TPROC-H build (SF=$SF)"
/usr/bin/time -p bash -c "source '$TOOLS_DIR/env.sh'; hdb tpch_build.tcl" > "$OUT/tpch_build.log" 2>&1
if grep -qE "^Error|FAILED" "$OUT/tpch_build.log"; then echo "TPC-H build failed"; exit 1; fi
echo "== TPROC-H power run"
"$TOOLS_DIR/snapshot.sh" "$TARGET" "$OUT/tpch_before"
"$TOOLS_DIR/capture.sh" "$TARGET" "$OUT/profile_tpch" 10 "$PROFILE_SECS" tpch & CAP=$!
hdb tpch_run.tcl > "$OUT/tpch_run.log"
wait $CAP
"$TOOLS_DIR/snapshot.sh" "$TARGET" "$OUT/tpch_after"
grep -E 'Completed|Geometric' "$OUT/tpch_run.log" | tail -2
PSQL "select datname, pg_size_pretty(pg_database_size(datname)) from pg_database where datname in ('tpcc','tpch')" | tee -a "$OUT/server.txt"
