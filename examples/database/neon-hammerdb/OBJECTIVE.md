# Make Neon faster on TPROC-C and TPROC-H on this Mac

Optimize the source code of [Neon](https://github.com/neondatabase/neon), the
open-source serverless Postgres (compute, pageserver, safekeepers), so that a
local Neon cluster on this machine runs HammerDB's TPC-derived workloads faster.

The target is **at least a 30% improvement** over the unmodified Neon build on
both throughput and latency:

| Objective | Metric | Direction |
|---|---|---|
| OLTP throughput | `tpcc_nopm`: TPROC-C new orders per minute, 16 virtual users | higher |
| OLTP commit latency | `tpcc_neworder_p95_ms`: p95 of the New-Order transaction | lower |
| Analytics | `tpch_geomean_s`: geometric mean of the 22 TPROC-H query times, SF10 | lower |

Specializing for this machine is allowed and encouraged: macOS on Apple
Silicon (18 cores, 64 GB, one internal NVMe SSD, APFS).

## The candidate

- `neon/` and `postgres-src/` are the candidate: the whole Neon tree at
  upstream commit `fa50421`, and its patched PostgreSQL 14–17 at
  `postgres-src/v1x/` (Neon's `vendor/postgres-v1x` submodules, pinned at the
  same commits). Any file in either may change: Rust services, the `neon`
  Postgres extension under `neon/pgxn/`, the Postgres forks, build flags.
  Before every build the benchmark copies `postgres-src/v1x` into
  `neon/vendor/postgres-v1x` (changed files only). Edit Postgres under
  `postgres-src/`; edits made directly in `neon/vendor/` are overwritten.
- `_ref_neon/` is a pristine copy for comparison. Do not edit it.
- `benchmark/` and `accuracy_checker/` are the trusted evaluator. They are
  read-only to you.

## How it is measured

`benchmark/benchmark.py` builds `neon/` in release mode, starts a fresh
`neon_local` cluster (storage broker, one pageserver, one safekeeper, one
PostgreSQL 17 compute) in a temporary directory, and runs:

1. TPROC-C: load 50 warehouses, then 16 virtual users with stored procedures,
   1 minute ramp-up and 5 minutes measured, with HammerDB's per-transaction
   latency profiler on.
2. TPROC-H: load scale factor 10, then one stream of all 22 queries.

The compute settings are fixed by the benchmark and are the same ones used for
vanilla PostgreSQL in the comparison: `shared_buffers=2GB`, `max_wal_size=8GB`,
`work_mem=64MB`, `max_parallel_workers_per_gather=8`, an 8 GB local file
cache (LFC), and the defaults `neon_local` writes for everything else.

Unmodified Neon measured on this machine (single runs, noisy):

| Metric | Neon baseline | Vanilla PostgreSQL 17, same settings |
|---|---|---|
| `tpcc_nopm` (16 VU) | ~40,000 | ~94,000 |
| New-Order p50 / p95 / p99 | 8.3 / 20.0 / 37.8 ms | 1.3 / 7.5 / 60.8 ms |
| `tpch_geomean_s` (22 queries, total) | 64.9 s (3,964 s) | 4.3 s (271 s) |

Reproduce a measurement yourself with `python3 benchmark/benchmark.py --vs-output /tmp/out.jsonl`
(about 2 hours on unmodified Neon, most of it the TPROC-H load and power run).
`NEON_HAMMERDB_QUICK=1` runs a ~8-minute smoke version (4 warehouses, 4 VU,
TPROC-H SF1 that fits in shared_buffers); its numbers are not comparable to
this table and say nothing about the pageserver or LFC read path.

## Rules

These are part of the task. The accuracy checker enforces what it can and the
judge reviews the diff for the rest.

1. **Keep every durability guarantee.** A commit acknowledged to the client
   must survive power loss, exactly as in unmodified Neon. The safekeeper must
   keep making WAL durable with the full drive-cache flush it uses today
   (`fcntl(F_FULLFSYNC)` on macOS) before acknowledging it. Do not switch to
   `F_BARRIERFSYNC`, plain `fsync`, `O_DSYNC`, no-sync modes, asynchronous
   commit, a weaker quorum, or acknowledging before the flush. Faster ways to
   reach the same guarantee are welcome: batching, pipelining, fewer
   round trips, less work per flush.
2. **Keep results correct.** Every query must return the same answer as
   standalone PostgreSQL on the same data, and TPC-C consistency must hold,
   including after a crash restart.
3. **No workload special-casing.** No code that detects HammerDB, TPC table or
   query names, or this benchmark's parameters; no result caching across
   statements that Postgres would not do; no skipping work the workload asked
   for. Improvements must help any Postgres workload of the same shape.
4. **Do not change the experimental conditions.** The benchmark owns the
   cluster topology, the compute settings and the workload. Do not make
   `neon_local` or the compute ignore them, and do not change defaults that the
   benchmark relies on to make the comparison fair (for example by shrinking
   what must be durable or by changing what `fsync` means).
5. **Changing a default is a source change and must be justified.** A better
   default for this machine is legitimate when it is not a durability or
   correctness trade-off. Explain it with measurements.

## Evidence expected per round

State the hypothesis and the mechanism, show the before/after numbers, and
attribute the change with a profile or counters: samply (`samply record -p
<pid>`), the pageserver's `/profile/cpu` endpoint, Prometheus `/metrics` from
the pageserver and safekeeper, and the `neon` extension's
`neon_perf_counters` and `neon_lfc_stats` views.
