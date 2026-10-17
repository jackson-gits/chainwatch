"""HTTP load test (spec 15.3): client-side end-to-end latency of POST /v1/score over localhost.

Usage:
  python bench/load_test.py --host-tag laptop --spawn                      # starts its own server (no replay)
  python bench/load_test.py --host-tag laptop --base-url http://127.0.0.1:8000

With --spawn the server is started with CHAINWATCH__FEATURES__MAX_LATENESS_S=3600 because concurrent requests
arrive slightly out of order; detection quality is not measured here. Load generator and server share the
machine, so network latency is excluded. Needs httpx (pure Python).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chainwatch.config import load, resolve_path  # noqa: E402
from chainwatch.data.io import iter_events  # noqa: E402

DEFAULT_SLICE = "data/bench/slice_medium_seed0.csv.gz"


def pct(values, q):
    s = sorted(values)
    return s[max(0, min(len(s) - 1, int(round(q * len(s) + 0.5)) - 1))] if s else None


async def run_level(client, base, events, concurrency: int, headers) -> dict:
    lat = []
    errors = 0
    idx = 0
    lock = asyncio.Lock()

    async def worker():
        nonlocal idx, errors
        while True:
            async with lock:
                if idx >= len(events):
                    return
                ev = events[idx]
                idx += 1
            a = time.perf_counter()
            try:
                r = await client.post(base + "/v1/score", json=ev, headers=headers)
                if r.status_code != 200:
                    errors += 1
            except Exception:
                errors += 1
            lat.append((time.perf_counter() - a) * 1e6)

    t0 = time.perf_counter()
    await asyncio.gather(*[worker() for _ in range(concurrency)])
    wall = time.perf_counter() - t0
    return {
        "concurrency": concurrency,
        "requests": len(events),
        "errors": errors,
        "requests_per_s": len(events) / wall,
        "latency_us": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "p99": pct(lat, 0.99), "max": max(lat)},
    }


async def run_batch(client, base, events, size: int, headers) -> dict:
    lat = []
    t0 = time.perf_counter()
    for i in range(0, len(events), size):
        a = time.perf_counter()
        await client.post(base + "/v1/score/batch", json={"events": events[i : i + size]}, headers=headers)
        lat.append((time.perf_counter() - a) * 1e6)
    wall = time.perf_counter() - t0
    return {
        "batch_size": size,
        "events": len(events),
        "events_per_s": len(events) / wall,
        "batch_latency_us": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95)},
    }


async def main_async(args, cfg) -> dict:
    import httpx

    path = resolve_path(args.slice)
    evs = []
    for le in iter_events(path):
        evs.append({k: v for k, v in le.event.to_dict().items()})
    headers = {"X-API-Key": os.environ["CHAINWATCH_API_KEY"]} if os.environ.get("CHAINWATCH_API_KEY") else {}
    levels = args.concurrency or list(cfg.bench.load_concurrency)
    n = args.requests or int(cfg.bench.load_requests)
    warm = int(cfg.bench.warmup_requests)
    base = args.base_url.rstrip("/")
    pos = 0

    def take(k):
        nonlocal pos
        chunk = evs[pos : pos + k]
        pos += k
        if len(chunk) < k:
            raise SystemExit("event slice too short for the requested load")
        return chunk

    limits = httpx.Limits(max_connections=max(levels) + 4, max_keepalive_connections=max(levels) + 4)
    async with httpx.AsyncClient(timeout=30.0, limits=limits) as client:
        before = (await client.get(base + "/v1/metrics", headers=headers)).json()
        await run_level(client, base, take(warm), 8, headers)
        results = []
        for c in levels:
            r = await run_level(client, base, take(n), c, headers)
            results.append(r)
            print(
                f"concurrency {c}: p50 {r['latency_us']['p50']:.0f} us, p95 {r['latency_us']['p95']:.0f} us, "
                f"{r['requests_per_s']:.0f} req/s, errors {r['errors']}"
            )
        batches = []
        for size in (1, 10, 100):
            k = min(n, 5000)
            batches.append(await run_batch(client, base, take(k), size, headers))
        after = (await client.get(base + "/v1/metrics", headers=headers)).json()
    return {
        "levels": results,
        "batch": batches,
        "server_metrics_before": before["latency_us"],
        "server_metrics_after": after["latency_us"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host-tag", required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--requests", type=int)
    ap.add_argument("--concurrency", type=int, nargs="*")
    ap.add_argument("--slice", default=DEFAULT_SLICE)
    ap.add_argument("--spawn", action="store_true", help="start a server (no replay) for the test")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    cfg = load()
    proc = None
    if args.spawn:
        env = dict(os.environ, CHAINWATCH__FEATURES__MAX_LATENESS_S="3600", PYTHONHASHSEED="0")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "chainwatch",
                "--log-level",
                "WARNING",
                "serve",
                "--no-replay",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
            ],
            cwd=ROOT,
            env=env,
        )
        args.base_url = f"http://127.0.0.1:{args.port}"
        import urllib.request

        for _ in range(120):
            try:
                with urllib.request.urlopen(args.base_url + "/healthz", timeout=1) as r:
                    if json.loads(r.read()).get("ready"):
                        break
            except Exception:
                pass
            time.sleep(0.5)
    try:
        res = asyncio.run(main_async(args, cfg))
    finally:
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=20)
    res.update(
        {
            "host_tag": args.host_tag,
            "kind": "http_load",
            "provenance": "ChainWorld",
            "machine": platform.machine(),
            "python": platform.python_version(),
            "slice": args.slice,
            "server": "uvicorn, 1 worker, h11, asyncio loop, --no-replay",
            "max_lateness_s": 3600 if args.spawn else "as configured on the server",
            "terminology": "end-to-end latency = client-side, HTTP over localhost (includes JSON, "
            "uvicorn and the network stack); not the same as engine latency",
            "same_machine": True,
        }
    )
    out = ROOT / "results" / f"bench_load_{args.host_tag}.json"
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
