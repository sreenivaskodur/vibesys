#!/usr/bin/env bash
# One-time: a neon_local cluster with TPROC-C (50 wh) and TPROC-H (SF10) loaded, using the
# binaries in NEON_ROOT (the baseline). Experiments branch from it (copy-on-write).
# Usage: NEON_ROOT=... GOLDEN_DIR=... setup_golden.sh
set -uo pipefail
source "$(dirname "$0")/../env.sh"
export NEON_REPO_DIR=${GOLDEN_DIR:?set GOLDEN_DIR}
export PGPORT=55432 PGSU=cloud_admin WAREHOUSES=${WAREHOUSES:-50} SF=${SF:-10}
export PGBIN=$NEON_ROOT/pg_install/v17/bin POSTGRES_DISTRIB_DIR=$NEON_ROOT/pg_install NEON_BIN=$NEON_ROOT/target/release
NL=$NEON_ROOT/target/release/neon_local
rm -rf "$NEON_REPO_DIR"; cd "$NEON_ROOT"
$NL init >/dev/null && $NL start && $NL tenant create --set-default --pg-version 17
$NL endpoint create main --pg-version 17 --pg-port $PGPORT
configure_endpoint "$NEON_REPO_DIR/endpoints/main"
$NL endpoint start main
trap '$NL endpoint stop main; $NL stop' EXIT
echo "load tpcc $(date +%T)"; hdb tpcc_build.tcl > "$NEON_REPO_DIR/golden_tpcc_build.log"
echo "load tpch $(date +%T)"; hdb tpch_build.tcl > "$NEON_REPO_DIR/golden_tpch_build.log"
"$PGBIN/psql" -h 127.0.0.1 -p $PGPORT -U $PGSU -d postgres -Atc "checkpoint; select datname, pg_size_pretty(pg_database_size(datname)) from pg_database where datname in ('tpcc','tpch')"
echo "golden ready $(date +%T)"
