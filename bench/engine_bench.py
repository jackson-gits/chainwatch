"""In-process engine benchmark (spec 15.2): stage timings and throughput of ScoringEngine.process.
Pure Python.

Usage:
  python bench/engine_bench.py --host-tag laptop --events 200000 --reps 3 [--compare] [--model-dir DIR]
  python bench/engine_bench.py --write-slice data/bench/slice_medium_seed0.csv.gz   # laptop, once

The event slice is the test split of world_medium_seed0 (from day 20), preceded by 24 hours of events that
warm the engine state and are not measured. Copy the slice file to other machines (it is git-ignored).
Measurement hygiene: run with PYTHONHASHSEED=0, no other heavy process; outliers are not trimmed; garbage
collection is left at its defaults.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chainwatch.config import load, resolve_path  # noqa: E402
from chainwatch.data.io import iter_events, write_events  # noqa: E402
from chainwatch.serving.metrics import rss_mb  # noqa: E402

DEFAULT_SLICE = "data/bench/slice_medium_seed0.csv.gz"
WORLD = "data/processed/world_medium_seed0/events.csv.gz"
TEST_START = 20 * 86400.0
WARM_S = 86400.0


def write_slice(out: str, n: int) -> dict:
    src = resolve_path(WORLD)
    kept = []
    measured = 0
    for le in iter_events(src):
        ts = le.event.ts
        if ts < TEST_START - WARM_S:
            continue
        kept.append(le)
        if ts >= TEST_START:
            measured += 1
            if measured >= n:
                break
    write_events(resolve_path(out), kept)
    return {"out": out, "events": len(kept), "measured_events": measured}


def load_slice(path: Path) -> tuple[list, list]:
    warm, meas = [], []
    for le in iter_events(path):
        (meas if le.event.ts >= TEST_START else warm).append(le.event)
    return warm, meas


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q * len(s) + 0.5)) - 1))
    return s[k]


def run_rep(cfg, model_dir, warm, meas, compare: bool, warmup_n: int) -> dict:
    from chainwatch.serving.engine import ScoringEngine

    eng = ScoringEngine(cfg, model_dir, compare=compare)
    for ev in warm:
        eng.process(ev, warmup=True)
    for ev in meas[:warmup_n]:
        eng.process(ev, warmup=True)
    stages = {"features": [], "model": [], "decision": [], "total": []}
    wall_total = []
    t0 = time.perf_counter()
    for ev in meas[warmup_n:]:
        a = time.perf_counter_ns()
        d, _, _ = eng.process(ev)
        b = time.perf_counter_ns()
        lat = d["latency_us"]
        for k in ("features", "model", "decision"):
            stages[k].append(lat[k])
        stages["total"].append(lat["total"])
        wall_total.append((b - a) / 1000.0)
    wall = time.perf_counter() - t0
    n = len(stages["total"])
    out = {
        "n": n,
        "events_per_s": n / wall if wall else None,
        "wall_s": wall,
        "rss_mb": rss_mb(),
        "engine": eng.engine_stats(),
        "rings": eng.grouper.stats(),
    }
    for k, vals in list(stages.items()) + [("process_call", wall_total)]:
        out[k] = {
            "p50": pct(vals, 0.50),
            "p95": pct(vals, 0.95),
            "p99": pct(vals, 0.99),
            "max": max(vals),
            "mean": statistics.fmean(vals),
        }
    edges = [10 * (2 ** (i / 2)) for i in range(28)]
    counts = [0] * (len(edges) + 1)
    for v in stages["total"]:
        i = 0
        while i < len(edges) and v > edges[i]:
            i += 1
        counts[i] += 1
    out["hist_total_us"] = {"edges": [round(e, 1) for e in edges], "counts": counts}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-tag")
    ap.add_argument("--events", type=int)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--model-dir")
    ap.add_argument("--slice", default=DEFAULT_SLICE)
    ap.add_argument("--write-slice")
    ap.add_argument("--priority", choices=["P0", "P1"])
    args = ap.parse_args()
    cfg = load(overrides=[f'features.priority="{args.priority}"'] if args.priority else None)
    n_events = args.events or int(cfg.bench.engine_events)
    if args.write_slice:
        print(json.dumps(write_slice(args.write_slice, n_events)))
        return 0
    if not args.host_tag:
        ap.error("--host-tag is required")
    path = resolve_path(args.slice)
    if not path.exists():
        print(f"missing {path}: create it on the laptop with --write-slice and copy it here")
        return 2
    warm, meas = load_slice(path)
    meas = meas[: n_events + int(cfg.bench.warmup_requests)]
    reps = []
    for r in range(args.reps):
        gc.collect()
        rep = run_rep(cfg, args.model_dir, warm, meas, args.compare, int(cfg.bench.warmup_requests))
        reps.append(rep)
        print(
            f"rep {r + 1}: p50 {rep['total']['p50']} us, p95 {rep['total']['p95']} us, "
            f"{rep['events_per_s']:.0f} events/s"
        )

    def med(stage, key):
        vals = [x[stage][key] for x in reps]
        return {"median": statistics.median(vals), "min": min(vals), "max": max(vals)}

    summary = {
        s: {k: med(s, k) for k in ("p50", "p95", "p99", "max")}
        for s in ("features", "model", "decision", "total", "process_call")
    }
    eps = [x["events_per_s"] for x in reps]
    res = {
        "host_tag": args.host_tag,
        "kind": "engine",
        "provenance": "ChainWorld",
        "mode": ("compare (B0, B1 and G2 scored per event)" if args.compare else "G2+R"),
        "priority": cfg.features.priority,
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
        "python": platform.python_version(),
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "gc": "defaults",
        "slice": str(args.slice),
        "warm_events": len(warm),
        "warmup_requests": int(cfg.bench.warmup_requests),
        "measured_events": reps[0]["n"],
        "reps": args.reps,
        "latency_us": summary,
        "events_per_s": {"median": statistics.median(eps), "min": min(eps), "max": max(eps)},
        "rss_mb_end": reps[-1]["rss_mb"],
        "engine": reps[-1]["engine"],
        "hist_total_us": reps[-1]["hist_total_us"],
        "per_rep": [{k: v for k, v in x.items() if k != "hist_total_us"} for x in reps],
        "terminology": "engine latency = time inside ScoringEngine.process (not end-to-end HTTP)",
    }
    suffix = "_compare" if args.compare else ""
    out = ROOT / "results" / f"bench_engine{suffix}_{args.host_tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    t = summary["total"]
    print(
        f"engine total: p50 {t['p50']['median']} us, p95 {t['p95']['median']} us, "
        f"p99 {t['p99']['median']} us; "
        f"{res['events_per_s']['median']:.0f} events/s -> {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
