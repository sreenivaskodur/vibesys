"""Shared harness for the neon-hammerdb benchmark and accuracy checker.

Owns three things, so the evaluator scripts never repeat them:

- building the candidate Neon tree (``neon/``) in release mode;
- a disposable local Neon cluster (``NeonCluster``) or a standalone Postgres
  (``VanillaPostgres``) whose lifetime is one ``with`` block, torn down on every
  exit path including signals;
- driving HammerDB scripts and parsing their results.

Everything here is trusted evaluator code: it is read-only to agents, and the
compute settings below are the fixed experimental conditions.

Failures raise ``HarnessError`` with the log tail that explains them.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path.cwd()
NEON = PROJECT_ROOT / "neon"
HERE = Path(__file__).resolve().parent
HAMMERDB_SCRIPTS = HERE / "hammerdb"
TOOLS_DIR = Path(
    os.environ.get(
        "NEON_HAMMERDB_TOOLS", Path.home() / ".cache" / "vibesys-neon-hammerdb" / "tools"
    )
)

# Fixed compute settings, identical for every candidate and for vanilla Postgres.
COMPUTE_SETTINGS = {
    "shared_buffers": "2GB",
    "max_connections": "200",
    "max_wal_size": "8GB",
    "checkpoint_timeout": "15min",
    "work_mem": "64MB",
    "maintenance_work_mem": "1GB",
    "max_parallel_workers_per_gather": "8",
    "max_parallel_workers": "16",
    "max_worker_processes": "32",
}
LFC_SIZE = "8GB"
NEON_PG_PORT = 55432
VANILLA_PG_PORT = 5499
# Environment the candidate must not be able to inherit from the operator's shell.
SCRUBBED_ENV_PREFIXES = ("NEON_SAFEKEEPER_", "NEON_PAGESERVER_")


class HarnessError(RuntimeError):
    """A build, cluster, or workload step failed; the message carries the evidence."""


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join(text.splitlines()[-lines:])


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(SCRUBBED_ENV_PREFIXES)}
    env["GIT_VERSION"] = env.get("VIBESYS_CANDIDATE_REVISION", "candidate")
    extra_paths = [str(Path.home() / ".cargo" / "bin")]
    if sys.platform == "darwin":
        # Homebrew keeps libpq/bison/flex out of PATH; Linux installs them system-wide.
        brew_paths = [f"{_brew_prefix(p)}/bin" for p in ("libpq", "bison", "flex")]
        extra_paths = [*brew_paths, *extra_paths, "/opt/homebrew/opt/rustup/bin"]
    env["PATH"] = ":".join([*extra_paths, env["PATH"]])
    return env


def _brew_prefix(formula: str) -> str:
    out = subprocess.run(["brew", "--prefix", formula], capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise HarnessError(f"Homebrew formula {formula!r} is missing; run benchmark/setup_tools.sh")
    return out.stdout.strip()


def run(
    cmd: list[str], *, cwd: Path | None = None, timeout: float, env: dict[str, str] | None = None
) -> str:
    """Run a command, return combined output, raise HarnessError on failure or timeout."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env or _env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise HarnessError(f"timed out after {timeout:.0f}s: {' '.join(cmd)}") from e
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise HarnessError(f"exit {proc.returncode}: {' '.join(cmd)}\n{_tail(out)}")
    return out


def ensure_tools() -> None:
    """Install the HammerDB toolchain into TOOLS_DIR once (shared across rounds).

    macOS builds HammerDB + Pgtcl + ticklecharts from source (no macOS release
    exists); Linux extracts HammerDB's release tarball, which bundles Tcl,
    pgtcl, and ticklecharts.
    """
    have_cli = (TOOLS_DIR / "HammerDB" / "hammerdbcli").exists()
    if sys.platform == "darwin":
        if have_cli and (TOOLS_DIR / "lib" / "pgtcl" / "pkgIndex.tcl").exists():
            return
        run(["bash", str(HERE / "setup_tools.sh"), str(TOOLS_DIR)], timeout=1800)
        return
    if have_cli:
        return
    run(["bash", str(HERE / "setup_tools_linux.sh"), str(TOOLS_DIR)], timeout=1800)


PG_VERSIONS = ("14", "15", "16", "17")
POSTGRES_SRC = PROJECT_ROOT / "postgres-src"


def sync_postgres_sources() -> None:
    """Mirror postgres-src/v1x into neon/vendor/postgres-v1x as real files (idempotent).

    The Postgres trees are workspace sources under postgres-src/ because the
    neon clone already holds empty submodule placeholders at vendor/. They are
    copied rather than symlinked: Postgres's VPATH configure resolves paths
    through a symlinked source dir and breaks the build tree. Only files whose
    size or mtime differ are copied (mtimes preserved), so make stays
    incremental; files removed from postgres-src/ are removed from vendor/.
    A full developer checkout (vendor/ populated, no postgres-src/) is left alone.
    """
    for v in PG_VERSIONS:
        dest = NEON / "vendor" / f"postgres-v{v}"
        src = POSTGRES_SRC / f"v{v}"
        if not src.is_dir():
            if (dest / "configure").exists():
                continue
            raise HarnessError(f"no Postgres {v} source: neither {src} nor {dest}/configure exists")
        if dest.is_symlink():  # layout from an earlier harness version
            dest.unlink()
        _mirror_tree(src, dest)


def _mirror_tree(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    wanted: set[Path] = set()
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        (dest / rel).mkdir(exist_ok=True)
        wanted.update(rel / d for d in dirs)
        for name in files:
            s, d = Path(root) / name, dest / rel / name
            wanted.add(rel / name)
            st = s.lstat()
            if d.is_symlink() or not d.exists():
                d.unlink(missing_ok=True)
            else:
                dt = d.lstat()
                if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                    continue
            if s.is_symlink():
                d.symlink_to(os.readlink(s))
            else:
                shutil.copy2(s, d)
    for root, dirs, files in os.walk(dest, topdown=False):
        rel = Path(root).relative_to(dest)
        for name in files:
            if rel / name not in wanted:
                (Path(root) / name).unlink()
        for name in dirs:
            if rel / name not in wanted:
                shutil.rmtree(Path(root) / name)


def build_candidate() -> float:
    """Release build of neon/ (Rust + patched Postgres). Incremental across rounds. Returns seconds."""
    if not (NEON / "Makefile").exists():
        raise HarnessError(f"{NEON} is missing the Neon source tree")
    sync_postgres_sources()
    start = time.monotonic()
    jobs = str(os.cpu_count() or 8)
    run(
        ["make", f"-j{jobs}", "-s", "BUILD_TYPE=release", "CARGO_BUILD_FLAGS=--features=testing"],
        cwd=NEON,
        timeout=5400,
    )
    return time.monotonic() - start


# ---------------------------------------------------------------- HammerDB


def hammerdb(script: str, env_extra: dict[str, str], *, timeout: float) -> str:
    """Run one HammerDB CLI script from benchmark/hammerdb/ and return its output."""
    env = _env()
    env.update(env_extra)
    env["TPCDIR"] = str(HAMMERDB_SCRIPTS)
    env["TCLLIBPATH"] = f"{TOOLS_DIR / 'lib'} {TOOLS_DIR / 'lib' / 'ticklecharts'}"
    out = run(
        ["./hammerdbcli", "auto", str(HAMMERDB_SCRIPTS / script)],
        cwd=TOOLS_DIR / "HammerDB",
        timeout=timeout,
        env=env,
    )
    if re.search(r"^Error|FINISHED FAILED|Virtual User \d+ FINISHED FAILED", out, re.M):
        raise HarnessError(f"HammerDB {script} reported a failure:\n{_tail(out)}")
    return out


def xtprof_log_path() -> Path:
    if sys.platform == "darwin":
        tmp = subprocess.run(
            ["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return Path(tmp) / "hdbxtprofile.log"
    return Path(tempfile.gettempdir()) / "hdbxtprofile.log"


@dataclass(frozen=True)
class TpccResult:
    nopm: int
    tpm: int
    latency_ms: dict[str, dict[str, float]]  # transaction -> {p50, p95, p99, avg, max}


@dataclass(frozen=True)
class TpchResult:
    total_s: float
    geomean_s: float
    query_s: dict[int, float]


def parse_tpcc(output: str, xtprof: str) -> TpccResult:
    m = re.search(r"System achieved (\d+) NOPM from (\d+) PostgreSQL TPM", output)
    if not m:
        raise HarnessError(f"TPROC-C produced no result line:\n{_tail(output)}")
    summary = xtprof.split("SUMMARY OF", 1)
    if len(summary) < 2:
        raise HarnessError("HammerDB time profile has no summary section")
    latency: dict[str, dict[str, float]] = {}
    current = None
    for line in summary[1].splitlines():
        proc = re.search(r"PROC: (\w+)", line)
        if proc:
            current = proc.group(1)
            latency[current] = {}
            continue
        if current:
            for key, val in re.findall(r"(AVG|MAX|P99|P95|P50):\s*([\d.]+)ms", line):
                latency[current][key.lower()] = float(val)
    if "NEWORD" not in latency or "p95" not in latency["NEWORD"]:
        raise HarnessError("time profile lacks NEWORD latency percentiles")
    return TpccResult(int(m.group(1)), int(m.group(2)), latency)


def parse_tpch(output: str) -> TpchResult:
    queries = {
        int(q): float(s)
        for q, s in re.findall(r"query (\d+) completed in ([\d.]+) seconds", output)
    }
    total = re.search(r"Completed 1 query set\(s\) in (\d+) seconds", output)
    geo = re.search(r"Geometric mean of query times returning rows \(\d+\) is ([\d.]+)", output)
    if len(queries) != 22 or not total or not geo:
        raise HarnessError(
            f"TPROC-H did not complete all 22 queries ({len(queries)} reported):\n{_tail(output)}"
        )
    return TpchResult(float(total.group(1)), float(geo.group(1)), queries)


def run_tpcc(
    endpoint: Endpoint, *, warehouses: int, vus: int, rampup_min: int, duration_min: int
) -> TpccResult:
    log = xtprof_log_path()
    log.unlink(missing_ok=True)
    env = endpoint.hammerdb_env() | {
        "WAREHOUSES": str(warehouses),
        "VUS": str(vus),
        "RAMPUP": str(rampup_min),
        "DURATION": str(duration_min),
    }
    out = hammerdb("tpcc_run.tcl", env, timeout=(rampup_min + duration_min) * 60 + 900)
    if not log.exists():
        raise HarnessError("HammerDB did not write its time profile")
    return parse_tpcc(out, log.read_text(errors="replace"))


def load_tpcc(endpoint: Endpoint, *, warehouses: int) -> float:
    start = time.monotonic()
    hammerdb(
        "tpcc_build.tcl", endpoint.hammerdb_env() | {"WAREHOUSES": str(warehouses)}, timeout=7200
    )
    return time.monotonic() - start


def load_tpch(endpoint: Endpoint, *, scale: int) -> float:
    start = time.monotonic()
    hammerdb("tpch_build.tcl", endpoint.hammerdb_env() | {"SF": str(scale)}, timeout=10800)
    return time.monotonic() - start


def run_tpch(endpoint: Endpoint, *, scale: int) -> TpchResult:
    return parse_tpch(
        hammerdb("tpch_run.tcl", endpoint.hammerdb_env() | {"SF": str(scale)}, timeout=10800)
    )


# ---------------------------------------------------------------- servers


@dataclass(frozen=True)
class Endpoint:
    port: int
    superuser: str
    pg_bin: Path

    def hammerdb_env(self) -> dict[str, str]:
        return {"PGPORT": str(self.port), "PGSU": self.superuser}

    def psql(self, sql: str, *, db: str = "postgres", timeout: float = 600) -> str:
        return run(
            [
                str(self.pg_bin / "psql"),
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-U",
                self.superuser,
                "-d",
                db,
                "-X",
                "-q",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                sql,
            ],
            timeout=timeout,
        )

    def psql_file(self, path: Path, *, db: str, timeout: float = 1800) -> str:
        return run(
            [
                str(self.pg_bin / "psql"),
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-U",
                self.superuser,
                "-d",
                db,
                "-X",
                "-q",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-f",
                str(path),
            ],
            timeout=timeout,
        )


@contextlib.contextmanager
def _terminate_on_signals():
    """Turn SIGTERM/SIGHUP into an exception so `finally` teardown runs."""

    def handler(signum, _frame):
        raise HarnessError(f"interrupted by signal {signum}")

    previous = {s: signal.signal(s, handler) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        yield
    finally:
        for s, h in previous.items():
            signal.signal(s, h)


class NeonCluster:
    """A fresh neon_local cluster (broker, pageserver, safekeeper, one PG17 compute) in a temp dir."""

    def __init__(self) -> None:
        self.repo = Path(tempfile.mkdtemp(prefix="vibesys-neon-", dir="/private/tmp"))
        self.nl = NEON / "target" / "release" / "neon_local"
        self.env = _env() | {
            "NEON_REPO_DIR": str(self.repo),
            "POSTGRES_DISTRIB_DIR": str(NEON / "pg_install"),
            "NEON_BIN": str(NEON / "target" / "release"),
        }
        self.endpoint = Endpoint(NEON_PG_PORT, "cloud_admin", NEON / "pg_install" / "v17" / "bin")

    def _nl(self, *args: str, timeout: float = 300) -> str:
        return run([str(self.nl), *args], cwd=NEON, timeout=timeout, env=self.env)

    def __enter__(self) -> NeonCluster:
        self._signals = _terminate_on_signals()
        self._signals.__enter__()
        try:
            shutil.rmtree(self.repo)
            self._nl("init")
            self._nl("start")
            self._nl("tenant", "create", "--set-default", "--pg-version", "17")
            self._nl(
                "endpoint", "create", "main", "--pg-version", "17", "--pg-port", str(NEON_PG_PORT)
            )
            ep_dir = self.repo / "endpoints" / "main"
            (ep_dir / "file_cache").mkdir(parents=True, exist_ok=True)
            lines = [f"{k}={v}" for k, v in COMPUTE_SETTINGS.items()] + [
                f"neon.file_cache_path='{ep_dir / 'file_cache' / 'file.cache'}'",
                f"neon.max_file_cache_size={LFC_SIZE}",
                f"neon.file_cache_size_limit={LFC_SIZE}",
            ]
            with (ep_dir / "postgresql.conf").open("a") as f:
                f.write("\n".join(lines) + "\n")
            self.start_endpoint()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def start_endpoint(self) -> None:
        self._nl("endpoint", "start", "main")

    def stop_endpoint(self, mode: str = "fast") -> None:
        self._nl("endpoint", "stop", "main", f"--mode={mode}")

    def __exit__(self, *_exc) -> None:
        try:
            for args in (
                ("endpoint", "stop", "main", "--mode=immediate"),
                ("stop", "--mode=immediate"),
            ):
                with contextlib.suppress(HarnessError):
                    self._nl(*args, timeout=120)
        finally:
            shutil.rmtree(self.repo, ignore_errors=True)
            self._signals.__exit__(None, None, None)


class VanillaPostgres:
    """A standalone PostgreSQL 17 from the candidate's own pg_install, same settings, in a temp dir."""

    def __init__(self) -> None:
        self.data = Path(tempfile.mkdtemp(prefix="vibesys-pg-", dir="/private/tmp")) / "data"
        self.bin = NEON / "pg_install" / "v17" / "bin"
        self.endpoint = Endpoint(VANILLA_PG_PORT, "postgres", self.bin)

    def __enter__(self) -> VanillaPostgres:
        self._signals = _terminate_on_signals()
        self._signals.__enter__()
        try:
            run(
                [str(self.bin / "initdb"), "-D", str(self.data), "-U", "postgres", "--auth=trust"],
                timeout=300,
            )
            with (self.data / "postgresql.conf").open("a") as f:
                f.write("\n".join(f"{k}={v}" for k, v in COMPUTE_SETTINGS.items()) + "\n")
            run(
                [
                    str(self.bin / "pg_ctl"),
                    "-D",
                    str(self.data),
                    "-o",
                    f"-p {VANILLA_PG_PORT}",
                    "-l",
                    str(self.data.parent / "postgres.log"),
                    "-w",
                    "start",
                ],
                timeout=300,
            )
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_exc) -> None:
        try:
            with contextlib.suppress(HarnessError):
                run(
                    [
                        str(self.bin / "pg_ctl"),
                        "-D",
                        str(self.data),
                        "-m",
                        "immediate",
                        "-w",
                        "stop",
                    ],
                    timeout=120,
                )
        finally:
            shutil.rmtree(self.data.parent, ignore_errors=True)
            self._signals.__exit__(None, None, None)
