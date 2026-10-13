#!/usr/bin/env bash
# Usage: capture.sh <target> <outdir> <delay_s> <dur_s> <dbname>
# Waits <delay_s> (past ramp-up), then profiles for <dur_s>:
#  - samply on the busiest Postgres backend (both targets)
#  - macOS `sample` on the 2nd-busiest backend (both targets)
#  - neon: samply on pageserver + safekeeper, and pageserver's built-in /profile/cpu (svg + pprof)
T=$1; O=$2; DELAY=$3; DUR=$4; DB=$5
mkdir -p "$O"; sleep "$DELAY"
backends() { ps -Ao pid=,pcpu=,command= | grep -E "postgres: $DB " | grep -v grep | sort -k2 -nr | awk '{print $1}'; }
B=($(backends)); ps -Ao pid,pcpu,command | grep -E "postgres: $DB " | grep -v grep | sort -k2 -nr | head -10 > "$O/backends.txt"
pids=()
rec() { samply record -p "$2" -s --unstable-presymbolicate -o "$O/samply_$1.json.gz" > "$O/samply_$1.log" 2>&1 & pids+=($!); }
[ -n "${B[0]:-}" ] && rec pg_backend "${B[0]}"
[ -n "${B[1]:-}" ] && (sample "${B[1]}" "$DUR" -file "$O/sample_pg_backend2.txt" >/dev/null 2>&1 &)
if [ "$T" = neon ]; then
  PS=$(pgrep -f 'target/release/pageserver' | head -1); SK=$(pgrep -f 'target/release/safekeeper' | head -1)
  [ -n "$PS" ] && rec pageserver "$PS"
  [ -n "$SK" ] && rec safekeeper "$SK"
  curl -s --max-time $((DUR+30)) "http://127.0.0.1:9898/profile/cpu?seconds=$DUR&format=svg" -o "$O/pageserver_cpu_flamegraph.svg" &
fi
sleep "$DUR"
for p in "${pids[@]}"; do kill -INT "$p" 2>/dev/null; done
wait
# pprof-format profile right after, for `go tool pprof`
[ "$T" = neon ] && curl -s --max-time $((DUR+30)) "http://127.0.0.1:9898/profile/cpu?seconds=$DUR&format=pprof" -o "$O/pageserver_cpu.pprof"
echo "capture done $(date '+%T')" > "$O/DONE"
