#!/usr/bin/env python3
"""Collect HammerDB TPROC-C/TPROC-H results, counter snapshots and profiles
from $RESULTS_DIR/{vanilla,neon} into one JSON blob and render the HTML report."""

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from bisect import bisect_right
from pathlib import Path

TPC = Path(__file__).resolve().parent  # holds report_template.html
RES = Path(os.environ.get("RESULTS_DIR", Path.cwd() / "neon-bench-results"))
OUT = RES / "report"
TARGETS = ["vanilla", "neon"]


# ---------- psql aligned-table parsing ----------
def psql_tables(path):
    """Return list of tables (list of dict rows) from psql aligned output."""
    if not path.exists():
        return []
    lines = path.read_text(errors="replace").splitlines()
    tables, i = [], 0
    while i < len(lines) - 1:
        if (
            re.match(r"^-+(\+-+)*$", lines[i + 1].strip())
            and "|" in lines[i]
            or (re.match(r"^-+$", lines[i + 1].strip()) and lines[i].strip())
        ):
            hdr = [h.strip() for h in lines[i].split("|")]
            rows, j = [], i + 2
            while j < len(lines) and not re.match(r"^\(\d+ rows?\)$", lines[j].strip()):
                cells = [c.strip() for c in lines[j].split("|")]
                if len(cells) == len(hdr):
                    rows.append(dict(zip(hdr, cells, strict=True)))
                j += 1
            tables.append(rows)
            i = j
        i += 1
    return tables


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def snapshot(prefix):
    """Flatten the interesting counters from one snapshot.sql.txt."""
    s = {}
    for t in psql_tables(Path(str(prefix) + ".sql.txt")):
        if not t:
            continue
        keys = t[0].keys()
        if "xact_commit" in keys:
            for r in t:
                d = r["datname"]
                for k in (
                    "xact_commit",
                    "blks_read",
                    "blks_hit",
                    "tup_inserted",
                    "tup_updated",
                    "tup_fetched",
                ):
                    s[f"db.{d}.{k}"] = num(r[k])
        elif "wal_bytes" in keys:
            for k in (
                "wal_records",
                "wal_fpi",
                "wal_bytes",
                "wal_buffers_full",
                "wal_write",
                "wal_sync",
            ):
                s[f"wal.{k}"] = num(t[0][k])
        elif "backend_type" in keys and "reads" in keys:
            for r in t:
                k = f"io.{r['backend_type']}.{r['object']}.{r['context']}"
                for c in ("reads", "writes", "extends", "hits", "evictions"):
                    s[f"{k}.{c}"] = num(r[c])
        elif "lfc_key" in keys:
            for r in t:
                s[f"lfc.{r['lfc_key']}"] = num(r["lfc_value"])
        elif "backpressure_throttling_us" in keys:
            s["bp.throttling_us"] = num(t[0]["backpressure_throttling_us"])
            s["working_set_pages"] = num(t[0]["working_set_pages"])
        elif "metric" in keys and "value" in keys:
            for r in t:
                b = r.get("bucket_le", "")
                s[f"pc.{r['metric']}" + (f"@{b}" if b else "")] = num(r["value"])
    return s


def delta(before, after):
    return {
        k: after[k] - before.get(k, 0)
        for k in after
        if after[k] is not None and before.get(k) is not None
    }


# ---------- HammerDB logs ----------
def tpcc_result(path):
    m = (
        re.search(
            r"System achieved (\d+) NOPM from (\d+) PostgreSQL TPM",
            path.read_text(errors="replace"),
        )
        if path.exists()
        else None
    )
    return {"nopm": int(m.group(1)), "tpm": int(m.group(2))} if m else None


def tpcc_latency(path):
    if not path.exists():
        return None
    txt = path.read_text(errors="replace")
    summ = txt.split("SUMMARY OF", 1)
    if len(summ) < 2:
        return None
    out, cur = {}, None
    for line in summ[1].splitlines():
        m = re.search(r"PROC: (\w+)", line)
        if m:
            cur = m.group(1)
            out[cur] = {}
            continue
        if cur:
            for k, v in re.findall(
                r"(CALLS|MIN|AVG|MAX|P99|P95|P75|P50|P25|SD|RATIO):\s*([\d.]+)", line
            ):
                out[cur][k.lower()] = float(v)
    return out


def tpch_result(path):
    if not path.exists():
        return None
    txt = path.read_text(errors="replace")
    q = {int(a): float(b) for a, b in re.findall(r"query (\d+) completed in ([\d.]+) seconds", txt)}
    tot = re.search(r"Completed 1 query set\(s\) in (\d+) seconds", txt)
    gm = re.search(r"Geometric mean of query times returning rows \(\d+\) is ([\d.]+)", txt)
    return {
        "queries": q,
        "total_s": int(tot.group(1)) if tot else None,
        "geomean_s": float(gm.group(1)) if gm else None,
    }


def build_time(path):
    if not path.exists():
        return None
    m = re.search(r"^real ([\d.]+)", path.read_text(errors="replace"), re.M)
    return float(m.group(1)) if m else None


def server_info(path):
    info = {"sizes": {}}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("PostgreSQL"):
                info["version"] = line.split(" (")[0]
            m = re.match(r"^(tpcc|tpch)\|(.+)$", line)
            if m:
                info["sizes"][m.group(1)] = m.group(2)
        lines = path.read_text().splitlines()
        if len(lines) > 1:
            info["shared_buffers"] = lines[1]
        for k, line in enumerate(lines):
            if line.endswith("file.cache") and k + 2 < len(lines):
                info["lfc_size"] = lines[k + 2]
    return info


# ---------- samply profiles ----------
def demangle(name):
    n = name[1:] if name.startswith("__Z") else name
    m = re.match(r"^_ZN(.*)E$", n)
    if not m:
        return name.lstrip("_") if name.startswith("_") and not name.startswith("__") else name
    body, parts, i = m.group(1), [], 0
    while i < len(body) and body[i].isdigit():
        j = i
        while body[j].isdigit():
            j += 1
        ln = int(body[i:j])
        parts.append(body[j : j + ln])
        i = j + ln
    if parts and re.match(r"^h[0-9a-f]{16}$", parts[-1]):
        parts = parts[:-1]
    s = "::".join(parts)
    for a, b in (
        ("$LT$", "<"),
        ("$GT$", ">"),
        ("$u20$", " "),
        ("$u7b$", "{"),
        ("$u7d$", "}"),
        ("$RF$", "&"),
        ("$C$", ","),
        ("$BP$", "*"),
        ("..", "::"),
        ("$u27$", "'"),
        ("$u5b$", "["),
        ("$u5d$", "]"),
    ):
        s = s.replace(a, b)
    return s


def _frame_namer(thread, libs, syms):
    """Return a cached frame-index -> (function, library) resolver for one samply thread."""
    strings = thread["stringArray"]
    ft, fn, rt = thread["frameTable"], thread["funcTable"], thread["resourceTable"]
    cache = {}

    def frame_name(fi):
        if fi in cache:
            return cache[fi]
        func = ft["func"][fi]
        res = fn["resource"][func]
        name = strings[fn["name"][func]]
        libname = "?"
        if res is not None and res >= 0:
            li = rt["lib"][res]
            if li is not None:
                lib = libs[li]
                libname = lib["name"]
                s = syms.get((lib.get("codeId") or "").upper())
                addr = ft["address"][fi]
                if s and addr is not None and addr >= 0:
                    k = bisect_right(s[0], addr) - 1
                    if k >= 0 and addr < s[1][k][0]:
                        name = s[1][k][1]
        r = (demangle(name), libname)
        cache[fi] = r
        return r

    return frame_name


def samply_profile(path, top=15):
    syms_path = Path(str(path).replace(".json.gz", ".json.syms.json"))
    if not path.exists() or path.stat().st_size < 200:
        return None
    prof = json.load(gzip.open(path))
    syms = {}
    if syms_path.exists():
        sj = json.load(open(syms_path))
        st = sj["string_table"]
        for lib in sj["data"]:
            tab = sorted(lib["symbol_table"], key=lambda e: e["rva"])
            syms[lib["code_id"].upper()] = (
                [e["rva"] for e in tab],
                [(e["rva"] + e.get("size", 1 << 30), st[e["symbol"]]) for e in tab],
            )
    libs = prof["libs"]
    self_cpu, self_wall, incl_wall = {}, {}, {}
    total_cpu = total_wall = 0
    for t in prof["threads"]:
        stk = t["stackTable"]
        frame_name = _frame_namer(t, libs, syms)

        samples = t["samples"]
        cpu = samples.get("threadCPUDelta") or [None] * samples["length"]
        for si in range(samples["length"]):
            sidx = samples["stack"][si]
            if sidx is None:
                continue
            w = (samples.get("weight") or [1] * samples["length"])[si] or 1
            c = cpu[si] or 0
            leaf = frame_name(stk["frame"][sidx])
            self_wall[leaf] = self_wall.get(leaf, 0) + w
            self_cpu[leaf] = self_cpu.get(leaf, 0) + c
            total_wall += w
            total_cpu += c
            seen, s = set(), sidx
            while s is not None:
                f = frame_name(stk["frame"][s])
                if f not in seen:
                    incl_wall[f] = incl_wall.get(f, 0) + w
                    seen.add(f)
                s = stk["prefix"][s]

    def topn(d, total, skip_roots=False):
        if not total:
            return []
        rows = [
            {"fn": k[0], "lib": k[1], "pct": round(100 * v / total, 2)}
            for k, v in sorted(d.items(), key=lambda kv: -kv[1])
        ]
        if (
            skip_roots
        ):  # frames present in ~every sample (main, PostmasterMain, tokio runtime...) say nothing
            rows = [r for r in rows if r["pct"] < 99.0]
        return rows[:top]

    return {
        "threads": len(prof["threads"]),
        "samples": total_wall,
        "cpu_ms": round(total_cpu / 1000, 1),
        "self_cpu": topn(self_cpu, total_cpu),
        "self_wall": topn(self_wall, total_wall),
        "incl_wall": topn(incl_wall, total_wall, skip_roots=True),
    }


def sample_top_of_stack(path, top=10):
    if not path.exists():
        return None
    txt = path.read_text(errors="replace")
    sec = txt.split("Sort by top of stack, same collapsed (when >= 5):", 1)
    if len(sec) < 2:
        return None
    rows = []
    for line in sec[1].splitlines()[1:]:
        m = re.match(r"^\s+(.+?)\s+\(in (.+?)\)\s+(\d+)$", line)
        if not m:
            if rows and not line.strip():
                break
            continue
        rows.append({"fn": demangle(m.group(1)), "lib": m.group(2), "n": int(m.group(3))})
    tot = sum(r["n"] for r in rows) or 1
    for r in rows:
        r["pct"] = round(100 * r["n"] / tot, 1)
    return rows[:top]


def profiles(d, files_dir, tag):
    if not d.exists():
        return None
    out = {
        "backends": (d / "backends.txt").read_text().strip().splitlines()
        if (d / "backends.txt").exists()
        else []
    }
    for proc in ("pg_backend", "pageserver", "safekeeper"):
        p = samply_profile(d / f"samply_{proc}.json.gz")
        if p:
            out[proc] = p
    out["sample_backend2"] = sample_top_of_stack(d / "sample_pg_backend2.txt")
    svg = d / "pageserver_cpu_flamegraph.svg"
    if svg.exists() and svg.stat().st_size > 1000:
        files_dir.mkdir(parents=True, exist_ok=True)
        dst = files_dir / f"{tag}_pageserver_flamegraph.svg"
        shutil.copy(svg, dst)
        out["flamegraph"] = f"files/{dst.name}"
    return out


def collect(target):
    r = RES / target
    if not r.exists():
        return None
    data = {
        "server": server_info(r / "server.txt"),
        "build_s": {
            "tpcc": build_time(r / "tpcc_build.log"),
            "tpch": build_time(r / "tpch_build.log"),
        },
    }
    data["tpcc"] = {}
    for f in sorted(
        r.glob("tpcc_run_vu*.log"), key=lambda p: int(re.search(r"vu(\d+)", p.name).group(1))
    ):
        vu = int(re.search(r"vu(\d+)", f.name).group(1))
        res = tpcc_result(f)
        if not res:
            continue
        res["latency"] = tpcc_latency(r / f"tpcc_latency_vu{vu}.log")
        res["delta"] = delta(
            snapshot(r / f"tpcc_vu{vu}_before"), snapshot(r / f"tpcc_vu{vu}_after")
        )
        data["tpcc"][vu] = res
    data["tpch"] = tpch_result(r / "tpch_run.log")
    if data["tpch"]:
        data["tpch"]["delta"] = delta(snapshot(r / "tpch_before"), snapshot(r / "tpch_after"))
    data["profiles"] = {}
    for d in sorted(r.glob("profile_*")):
        p = profiles(d, OUT / "files", f"{target}_{d.name.replace('profile_', '')}")
        if p:
            data["profiles"][d.name.replace("profile_", "")] = p
    return data


def _sysctl(key):
    out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, check=False)
    return out.stdout.strip()


def machine_summary():
    mem = _sysctl("hw.memsize")
    mem_gb = f"{int(mem) // 2**30} GB" if mem.isdigit() else "? GB"
    return f"{_sysctl('machdep.cpu.brand_string') or 'unknown CPU'} · {_sysctl('hw.ncpu')} cores · {mem_gb}"


def neon_revision():
    root = os.environ.get("NEON_ROOT")
    if not root:
        return None
    out = subprocess.run(
        ["git", "-C", root, "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() or None


def main():
    meta = {
        "machine": machine_summary(),
        "neon_revision": neon_revision(),
        "notes": [n.strip() for n in os.environ.get("REPORT_NOTES", "").split(";") if n.strip()],
    }
    chain = RES / "chain.log"
    if chain.exists():
        meta["chain"] = chain.read_text().strip().splitlines()
    data = {"meta": meta, "targets": {t: collect(t) for t in TARGETS}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "data.json").write_text(json.dumps(data, indent=1, default=str))
    tpl = (TPC / "report_template.html").read_text()
    (OUT / "tpc-report.html").write_text(
        tpl.replace("/*__DATA__*/null", json.dumps(data, default=str))
    )
    print("wrote", OUT / "tpc-report.html")


if __name__ == "__main__":
    sys.exit(main())
