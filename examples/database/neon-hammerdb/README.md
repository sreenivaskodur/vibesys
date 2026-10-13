# neon-hammerdb: optimize Neon for TPROC-C and TPROC-H on macOS

A VibeSys input that optimizes the source code of
[Neon](https://github.com/neondatabase/neon) (serverless Postgres: compute,
pageserver, safekeepers) for HammerDB's TPC-derived workloads, run as a local
`neon_local` cluster on an Apple Silicon Mac. The goal is a 30% improvement in
TPROC-C throughput and New-Order latency and in TPROC-H query time, without
weakening durability or correctness. See [OBJECTIVE.md](OBJECTIVE.md) for the
task and its rules.

## Layout

| Path | Role |
|---|---|
| `vibesys.input.toml` | Pins the candidate: Neon `fa50421` at `neon/`, its four patched Postgres trees (submodules upstream) at `neon/vendor/postgres-v1x`, and a pristine `_ref_neon/` for the durability diff. |
| `objectives.toml` | `tpcc_nopm` (max), `tpcc_neworder_p95_ms` (min), `tpch_geomean_s` (min). |
| `benchmark/benchmark.py` | Trusted benchmark. Builds `neon/`, starts a fresh cluster, runs TPROC-C (50 warehouses, 16 VU, 1+5 min) and TPROC-H (SF10, 22 queries), emits protocol-2 records. |
| `benchmark/harness.py` | Shared build, cluster and HammerDB driver, with the fixed compute settings. |
| `benchmark/hammerdb/*.tcl` | HammerDB v6.0 CLI scripts (schema build and timed runs). |
| `benchmark/setup_tools.sh` | Builds HammerDB, Pgtcl and ticklecharts for macOS into `~/.cache/vibesys-neon-hammerdb/tools`. |
| `accuracy_checker/checker.py` | Gates: durability diff, build, PostgreSQL `make check`, TPC-H result equality Neon vs standalone PG, TPC-C consistency across a crash restart. |
| `tools/` | Manual analysis tools, not used by the evaluator (below). |
| `experiment/` | `launch.sh` / `resume.sh` / `experiment.env.sh` for a VibeSys run, in the style of the GCP experiment scripts. |

## Running the VibeSys experiment

```bash
examples/database/neon-hammerdb/experiment/launch.sh   # fresh run, in a tmux pane
examples/database/neon-hammerdb/experiment/resume.sh   # after quitting or a TUI restart
```

The effective command is:

```bash
uv run vibesys --runs-dir ~/vibesys-runs/neon-hammerdb --local --backend cpu --profiler none \
  --constraint "<CONSTRAINT_MACHINE>" --constraint "<CONSTRAINT_DURABILITY>" \
  --input examples/database/neon-hammerdb --max-rounds 10
```

| Flag | Why |
|---|---|
| `--runs-dir` | The example lives inside the vibesys checkout, so VibeSys materializes an isolated project (own git repo) under this collection. |
| `--local` | Keep the project on disk; do not publish it to GitHub. |
| `--backend cpu` | No accelerator; the workload is CPU and disk bound. |
| `--profiler none` | The `database` domain only allows `none`; agents capture their own profiles with samply and Neon's built-in endpoints, as the objective asks. |
| `--constraint` ×2 | Machine specialization with A/B evidence, and the durability floor. Defined once in `experiment.env.sh` so resume repeats them byte-identically. |

Host prerequisites: Homebrew, Rust (rustup), and the packages in
`benchmark/setup_tools.sh` (`tcl-tk libpq autoconf samply`) plus Neon's build
dependencies (`protobuf openssl flex bison icu4c pkg-config m4`). One evaluation
takes about 45 minutes after the first build (about 15 minutes).

The macOS agent sandbox denies SysV IPC, which PostgreSQL needs on macOS, so
sandboxed agents cannot start a cluster themselves. The trusted benchmark and
checker run on the host and are unaffected. `VIBESYS_AGENT_SANDBOX=0` lets
agents run the cluster, at the cost of removing their sandbox.

## Running the benchmark or checker directly

From a materialized project root (the directory holding `neon/`):

```bash
python3 benchmark/benchmark.py --vs-output /tmp/neon.jsonl
NEON_HAMMERDB_QUICK=1 python3 benchmark/benchmark.py --vs-output /tmp/smoke.jsonl   # ~5 min, not comparable
python3 accuracy_checker/checker.py
```

## Manual tools

All take `NEON_ROOT` (a built Neon checkout) and write under `RESULTS_DIR`
(default `./neon-bench-results`). Run `benchmark/setup_tools.sh` first.

| Tool | Use |
|---|---|
| `tools/run_tpc.sh vanilla\|neon` | Full comparison run: TPROC-C at 8/16/32 VU and TPROC-H, with counter snapshots, HammerDB latency profiles, samply captures of the busiest backend, pageserver and safekeeper, and the pageserver's CPU flame graph. |
| `tools/make_report.py` | Turns `RESULTS_DIR/{vanilla,neon}` into `RESULTS_DIR/report/tpc-report.html` (charts, latency tables, counters, profile top-functions). |
| `tools/exp/setup_golden.sh` | Loads TPROC-C and TPROC-H once into a golden `neon_local` cluster. |
| `tools/exp/run_exp.sh <label> <neon_root>` | One A/B run on a copy-on-write branch of the golden data, with any build tree's binaries. Alternate baseline and candidate runs to cancel drift. |
| `tools/exp/fsync_bench.c`, `fsync_conc.c` | Latency of `fsync`, `F_FULLFSYNC`, `F_BARRIERFSYNC` and `O_DSYNC`, single and concurrent. |

## Baseline measurements

Apple M5 Pro, 18 cores, 64 GB, macOS 26.5. Single runs; the Neon test suite was
running on the same machine, which adds noise and inflates `F_FULLFSYNC`.

| TPROC-C | Vanilla PG17 NOPM | Neon NOPM | Neon New-Order p50 / p95 / p99 |
|---|---|---|---|
| 8 VU | 98,732 | 23,515 | 7.9 / 16.6 / 32.9 ms |
| 16 VU | 93,568 | 40,253 | 8.3 / 20.0 / 37.8 ms |
| 32 VU | 113,043 | 37,117 | 14.1 / 43.9 / 112.9 ms |

TPROC-H SF10, vanilla PG17: 271 s for 22 queries, geometric mean 4.32 s.

What the profiles showed at 16 VU:

- Vanilla PG17 is commit-bound: the busiest backend spends ~78% of wall time in
  `XLogFlush` waiting on the WAL write lock.
- Neon is bound by the safekeeper's WAL flush. Rust's `sync_data()` is
  `fcntl(F_FULLFSYNC)` on macOS, about 4.4 ms per call under load, and the
  safekeeper was flushing ~90% of the time. Vanilla PostgreSQL on macOS uses
  `open_datasync`, which does not flush the drive cache (~0.03 ms per write).
