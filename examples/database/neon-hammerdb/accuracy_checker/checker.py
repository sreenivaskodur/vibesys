"""Accuracy checker for the neon-hammerdb task. Exit 0 = pass, 1 = defect, 2 = setup error.

Gates, in order (cheapest first):

1. durability  Static diff of neon/ against the pristine _ref_neon/: rejects changes
               that weaken how WAL or data reach stable storage (safekeeper flush
               primitive, no-sync defaults, compute commit settings).
2. pg-regress  PostgreSQL 17's own regression suite (`make check`) on the candidate's
               patched Postgres.
3. tpch        TPC-H scale factor 1 loaded into a Neon cluster; the 22 queries must
               return byte-identical results on Neon and on a standalone Postgres
               restored from a dump of the same data.
4. tpcc        A short TPROC-C run on Neon, then the four TPC-C consistency
               conditions, before and after an immediate (crash) compute restart.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import harness  # noqa: E402  (shared trusted harness, sibling directory)

HERE = Path(__file__).resolve().parent
REF = harness.PROJECT_ROOT / "_ref_neon"

# Paths whose changes can alter durability, and patterns an added line may not contain.
DURABILITY_SCOPE = (
    "safekeeper/",
    "libs/utils/src/crashsafe.rs",
    "control_plane/",
    "compute_tools/",
    "pgxn/neon/walproposer",
)
FORBIDDEN_ADDED = [
    (re.compile(r"F_BARRIERFSYNC"), "replaces the full-cache-flush WAL sync with a write barrier"),
    (re.compile(r"\bO_DSYNC\b|\bO_SYNC\b"), "switches WAL durability to O_DSYNC/O_SYNC writes"),
    (re.compile(r"no_sync\s*[:=]\s*true|\"--no-sync\""), "turns on a no-sync mode"),
    (
        re.compile(r"synchronous_commit\s*[=:]\s*['\"]?(off|local|remote_write)"),
        "relaxes synchronous_commit",
    ),
    (re.compile(r"\bfsync\s*=\s*['\"]?off"), "disables fsync"),
    (re.compile(r"synchronous_standby_names\s*=\s*['\"]{2}"), "drops the safekeeper sync quorum"),
]
SYNC_CALLS = re.compile(
    r"\b(sync_data|sync_all|fdatasync|fsync_file|fdatasync_file|durable_rename|fsync_async)\b"
)

TPCC_CONSISTENCY = [
    (
        "W_YTD = sum(D_YTD)",
        "select count(*) from warehouse w where w.w_ytd <> (select sum(d_ytd) from district d where d.d_w_id = w.w_id)",
    ),
    (
        "D_NEXT_O_ID - 1 = max(O_ID) = max(NO_O_ID)",
        "select count(*) from district d where d.d_next_o_id - 1 <> (select max(o_id) from orders o where o.o_w_id = d.d_w_id and o.o_d_id = d.d_id)"
        " or d.d_next_o_id - 1 <> coalesce((select max(no_o_id) from new_order n where n.no_w_id = d.d_w_id and n.no_d_id = d.d_id), d.d_next_o_id - 1)",
    ),
    (
        "count(NO_O_ID) = max(NO_O_ID) - min(NO_O_ID) + 1",
        "select count(*) from (select no_w_id, no_d_id, count(*) c, max(no_o_id) - min(no_o_id) + 1 r from new_order group by 1, 2) x where c <> r",
    ),
    (
        "sum(O_OL_CNT) = count(order_line)",
        "select count(*) from (select o_w_id, o_d_id, sum(o_ol_cnt) s from orders group by 1, 2) o"
        " join (select ol_w_id, ol_d_id, count(*) c from order_line group by 1, 2) l on o.o_w_id = l.ol_w_id and o.o_d_id = l.ol_d_id where o.s <> l.c",
    ),
]


class Defect(Exception):
    """The candidate is wrong (exit 1), as opposed to the harness failing (exit 2)."""


def _scope_files(root: Path) -> set[Path]:
    files: set[Path] = set()
    for entry in DURABILITY_SCOPE:
        base = root / entry.rstrip("/")
        candidates = (
            [base]
            if base.is_file()
            else base.rglob("*")
            if base.is_dir()
            else root.glob(entry + "*")
        )
        files |= {p.relative_to(root) for p in candidates if p.is_file()}
    return files


def gate_durability() -> None:
    if not REF.is_dir():
        raise harness.HarnessError(f"pristine reference {REF} is missing")
    problems = []
    for cand in _scope_files(harness.NEON) | _scope_files(REF):
        rel = cand.as_posix()
        cand = harness.NEON / rel
        if not cand.is_file():
            problems.append(f"{rel}: deleted")
            continue
        ref = REF / rel
        old = ref.read_text(errors="replace").splitlines() if ref.exists() else []
        new = cand.read_text(errors="replace").splitlines()
        if old == new:
            continue
        for line in difflib.unified_diff(old, new, lineterm="", n=0):
            if line.startswith("+") and not line.startswith("+++"):
                for pat, why in FORBIDDEN_ADDED:
                    if pat.search(line):
                        problems.append(f"{rel}: {why}: {line[1:].strip()[:160]}")
            elif (
                line.startswith("-")
                and not line.startswith("---")
                and rel.startswith("safekeeper/")
            ):
                if SYNC_CALLS.search(line) and sum(bool(SYNC_CALLS.search(x)) for x in new) < sum(
                    bool(SYNC_CALLS.search(x)) for x in old
                ):
                    problems.append(
                        f"{rel}: removes a WAL/data sync call: {line[1:].strip()[:160]}"
                    )
    if problems:
        raise Defect("durability guard:\n" + "\n".join(sorted(set(problems))))


def gate_pg_regress() -> None:
    build = harness.NEON / "build" / "v17"
    try:
        harness.run(["make", "-C", str(build), "-s", "check"], timeout=3600)
    except harness.HarnessError as e:
        raise Defect(f"PostgreSQL regression suite failed:\n{e}") from e


def _query_digests(ep: harness.Endpoint) -> dict[str, str]:
    out = {}
    for q in sorted(HERE.glob("tpch_queries/*.sql"), key=lambda p: int(p.stem)):
        rows = ep.psql_file(q, db="tpch")
        out[q.stem] = hashlib.sha256(rows.encode()).hexdigest()
    return out


def gate_tpch() -> None:
    dump = Path(harness.tempfile.mkdtemp(prefix="vibesys-tpch-", dir="/private/tmp")) / "tpch.dump"
    try:
        with harness.NeonCluster() as cluster:
            harness.load_tpch(cluster.endpoint, scale=1)
            neon = _query_digests(cluster.endpoint)
            harness.run(
                [
                    str(cluster.endpoint.pg_bin / "pg_dump"),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(harness.NEON_PG_PORT),
                    "-U",
                    "cloud_admin",
                    "-Fc",
                    "-f",
                    str(dump),
                    "tpch",
                ],
                timeout=1800,
            )
        with harness.VanillaPostgres() as pg:
            pg.endpoint.psql("create database tpch")
            harness.run(
                [
                    str(pg.bin / "pg_restore"),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(harness.VANILLA_PG_PORT),
                    "-U",
                    "postgres",
                    "-d",
                    "tpch",
                    "--no-owner",
                    "-j",
                    "8",
                    str(dump),
                ],
                timeout=1800,
            )
            vanilla = _query_digests(pg.endpoint)
    finally:
        harness.shutil.rmtree(dump.parent, ignore_errors=True)
    wrong = [q for q in vanilla if neon.get(q) != vanilla[q]]
    if wrong:
        raise Defect(
            f"TPC-H results differ between Neon and standalone Postgres for queries {', '.join(wrong)}"
        )


def _tpcc_consistent(ep: harness.Endpoint, when: str) -> None:
    for name, sql in TPCC_CONSISTENCY:
        bad = ep.psql(sql, db="tpcc").strip()
        if bad != "0":
            raise Defect(f"TPC-C consistency '{name}' violated in {bad} rows ({when})")


def gate_tpcc() -> None:
    with harness.NeonCluster() as cluster:
        ep = cluster.endpoint
        harness.load_tpcc(ep, warehouses=4)
        harness.run_tpcc(ep, warehouses=4, vus=4, rampup_min=0, duration_min=1)
        _tpcc_consistent(ep, "after run")
        orders = ep.psql("select count(*) from orders", db="tpcc").strip()
        cluster.stop_endpoint(mode="immediate")
        cluster.start_endpoint()
        _tpcc_consistent(ep, "after immediate restart")
        after = ep.psql("select count(*) from orders", db="tpcc").strip()
        if after != orders:
            raise Defect(f"committed orders lost across restart: {orders} before, {after} after")


def gate_build() -> None:
    try:
        harness.build_candidate()
    except harness.HarnessError as e:
        raise Defect(f"candidate does not build:\n{e}") from e


GATES = [
    ("durability", gate_durability),
    ("build", gate_build),
    ("pg-regress", gate_pg_regress),
    ("tpch", gate_tpch),
    ("tpcc", gate_tpcc),
]


def main() -> int:
    try:
        harness.ensure_tools()
    except harness.HarnessError as e:
        print(f"SETUP ERROR (tools): {e}")
        return 2
    for name, gate in GATES:
        try:
            gate()
        except Defect as e:
            print(f"FAIL {name}: {e}")
            return 1
        except harness.HarnessError as e:
            print(f"SETUP ERROR ({name}): {e}")
            return 2
        print(f"PASS {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
