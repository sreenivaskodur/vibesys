"""Benchmark: HammerDB TPROC-C and TPROC-H against a fresh local Neon cluster.

Builds the candidate ``neon/`` tree, starts a disposable neon_local cluster with
the fixed compute settings in ``harness.py``, loads TPROC-C (50 warehouses) and
TPROC-H (scale factor 10), and measures:

- TPROC-C at 16 virtual users: 1 min ramp-up, 5 min timed run, with HammerDB's
  per-transaction latency profiler on;
- TPROC-H: one stream of all 22 queries.

Writes a VibeSys protocol-2 record stream to the path given by ``--vs-output``.

Usage: python3 benchmark/benchmark.py --vs-output FILE
       (env NEON_HAMMERDB_QUICK=1 shrinks the workload for local smoke tests;
        the official evaluation never sets it)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402  (sibling module of a standalone script)

METRICS = {
    "tpcc_nopm": {"unit": "new orders/min", "direction": "max"},
    "tpcc_tpm": {"unit": "transactions/min", "direction": "max"},
    "tpcc_neworder_p50_ms": {"unit": "ms", "direction": "min"},
    "tpcc_neworder_p95_ms": {"unit": "ms", "direction": "min"},
    "tpcc_neworder_p99_ms": {"unit": "ms", "direction": "min"},
    "tpcc_payment_p95_ms": {"unit": "ms", "direction": "min"},
    "tpch_geomean_s": {"unit": "s", "direction": "min"},
    "tpch_total_s": {"unit": "s", "direction": "min"},
    "tpcc_load_s": {"unit": "s", "direction": "min"},
    "tpch_load_s": {"unit": "s", "direction": "min"},
}

FULL = {"warehouses": 50, "vus": 16, "rampup_min": 1, "duration_min": 5, "scale": 10}
QUICK = {"warehouses": 4, "vus": 4, "rampup_min": 0, "duration_min": 1, "scale": 1}


def measure(cfg: dict[str, int]) -> dict[str, float]:
    harness.ensure_tools()
    harness.build_candidate()
    with harness.NeonCluster() as cluster:
        ep = cluster.endpoint
        tpcc_load = harness.load_tpcc(ep, warehouses=cfg["warehouses"])
        tpcc = harness.run_tpcc(
            ep,
            warehouses=cfg["warehouses"],
            vus=cfg["vus"],
            rampup_min=cfg["rampup_min"],
            duration_min=cfg["duration_min"],
        )
        tpch_load = harness.load_tpch(ep, scale=cfg["scale"])
        tpch = harness.run_tpch(ep, scale=cfg["scale"])
    return {
        "tpcc_nopm": float(tpcc.nopm),
        "tpcc_tpm": float(tpcc.tpm),
        "tpcc_neworder_p50_ms": tpcc.latency_ms["NEWORD"]["p50"],
        "tpcc_neworder_p95_ms": tpcc.latency_ms["NEWORD"]["p95"],
        "tpcc_neworder_p99_ms": tpcc.latency_ms["NEWORD"]["p99"],
        "tpcc_payment_p95_ms": tpcc.latency_ms["PAYMENT"]["p95"],
        "tpch_geomean_s": tpch.geomean_s,
        "tpch_total_s": tpch.total_s,
        "tpcc_load_s": round(tpcc_load, 1),
        "tpch_load_s": round(tpch_load, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vs-output", required=True, type=Path)
    args = parser.parse_args()
    cfg = QUICK if os.environ.get("NEON_HAMMERDB_QUICK") == "1" else FULL
    records: list[dict] = [{"kind": "hello", "protocol": 2, "metrics": METRICS}]
    try:
        values = measure(cfg)
        records.append({"kind": "result", "label": "", "values": values})
        status = 0
    except harness.HarnessError as e:
        records = [{"kind": "error", "message": str(e)[-4000:]}]
        status = 1
    args.vs_output.write_text("".join(json.dumps(r) + "\n" for r in records))
    print(json.dumps(records[-1], indent=1))
    return status


if __name__ == "__main__":
    sys.exit(main())
