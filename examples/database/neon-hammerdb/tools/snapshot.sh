#!/usr/bin/env bash
# Usage: snapshot.sh <target> <outfile-prefix>   (cheap counter snapshot; run before/after each workload)
T=$1; P=$2
PSQL="$PGBIN/psql -h 127.0.0.1 -p $PGPORT -U $PGSU -d postgres -X -q"
{
  echo "-- $(date '+%F %T')"
  $PSQL -c "select now(), datname, xact_commit, xact_rollback, blks_read, blks_hit, tup_inserted, tup_updated, tup_fetched from pg_stat_database where datname in ('tpcc','tpch')"
  $PSQL -c "select * from pg_stat_wal"
  $PSQL -c "select backend_type, object, context, reads, read_time, writes, write_time, extends, hits, evictions, fsyncs from pg_stat_io where reads>0 or writes>0 or hits>0"
  if [ "$T" = neon ]; then
    $PSQL -c "create extension if not exists neon" 2>/dev/null
    $PSQL -c "select * from neon_perf_counters"
    $PSQL -c "select * from neon_lfc_stats"
    $PSQL -c "select backpressure_throttling_time() as backpressure_throttling_us, approximate_working_set_size(false) as working_set_pages"
    $PSQL -c "select * from backpressure_lsns()"
  fi
} > "$P.sql.txt" 2>&1
if [ "$T" = neon ]; then
  curl -s --max-time 20 http://127.0.0.1:9898/metrics > "$P.pageserver.prom"
  curl -s --max-time 20 http://127.0.0.1:7676/metrics > "$P.safekeeper.prom"
fi
