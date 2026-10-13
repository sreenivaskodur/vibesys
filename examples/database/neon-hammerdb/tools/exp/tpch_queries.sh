#!/usr/bin/env bash
# Time selected TPC-H queries (accuracy_checker/tpch_queries/<n>.sql, fixed parameters) via psql.
# Usage: PGBIN=... PGPORT=... PGSU=... tpch_queries.sh <outfile> <q> [q...]
# Prints "Q<n> <seconds>" per query and writes the same to <outfile>; exits non-zero on any failure.
set -euo pipefail
OUT=$1; shift
QDIR=$(cd "$(dirname "$0")/../../accuracy_checker/tpch_queries" && pwd)
: > "$OUT"
for q in "$@"; do
  start=$(python3 -c 'import time; print(time.monotonic())')
  "$PGBIN/psql" -h 127.0.0.1 -p "$PGPORT" -U "$PGSU" -d tpch -X -q -v ON_ERROR_STOP=1 -f "$QDIR/$q.sql" > /dev/null
  end=$(python3 -c 'import time; print(time.monotonic())')
  printf "Q%s %.2f\n" "$q" "$(python3 -c "print($end - $start)")" | tee -a "$OUT"
done
