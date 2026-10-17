"""Latency histograms, throughput counters and process memory (spec 13.5). Stdlib only.

Terminology kept in every report: *engine* latency is time inside ``ScoringEngine.process``;
*end-to-end* latency is measured by the HTTP load test from the client side.
"""

from __future__ import annotations

import math
import os
import sys
import time
from collections import deque

STAGES = ("features", "model", "decision", "total")


class LatencyHistogram:
    """Log-spaced buckets (ratio sqrt(2)) from 10 us plus an exact ring buffer for exact percentiles."""

    def __init__(self, max_ms: float = 2000.0, exact_n: int = 20000):
        self.edges_us: list[float] = []
        v = 10.0
        while v < max_ms * 1000.0:
            self.edges_us.append(v)
            v *= math.sqrt(2.0)
        self.counts = [0] * (len(self.edges_us) + 1)
        self.recent: deque = deque(maxlen=exact_n)
        self.n = 0
        self.max_us = 0.0

    def add(self, us: float) -> None:
        self.n += 1
        if us > self.max_us:
            self.max_us = us
        self.recent.append(us)
        lo, hi = 0, len(self.edges_us)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.edges_us[mid] < us:
                lo = mid + 1
            else:
                hi = mid
        self.counts[lo] += 1

    def percentiles(self) -> dict:
        if not self.recent:
            return {"p50": None, "p95": None, "p99": None, "max": None, "n": self.n}
        s = sorted(self.recent)
        k = len(s)

        def p(q):
            return s[min(k - 1, max(0, int(math.ceil(q * k)) - 1))]

        return {
            "p50": round(p(0.50), 1),
            "p95": round(p(0.95), 1),
            "p99": round(p(0.99), 1),
            "max": round(self.max_us, 1),
            "n": self.n,
        }

    def buckets(self) -> dict:
        return {"edges_us": [round(e, 1) for e in self.edges_us], "counts": list(self.counts)}


class Throughput:
    """Events per second over the last 10 s and 60 s (1-second wall-clock buckets, 120 s kept)."""

    def __init__(self, keep_s: int = 120):
        self.keep = keep_s
        self.buckets: deque = deque()
        self.total = 0

    def add(self, n: int = 1, now: float | None = None) -> None:
        sec = int(now if now is not None else time.monotonic())
        if self.buckets and self.buckets[-1][0] == sec:
            self.buckets[-1][1] += n
        else:
            self.buckets.append([sec, n])
        while self.buckets and self.buckets[0][0] < sec - self.keep:
            self.buckets.popleft()
        self.total += n

    def rate(self, window_s: int, now: float | None = None) -> float:
        sec = int(now if now is not None else time.monotonic())
        tot = sum(c for s, c in self.buckets if sec - window_s < s <= sec)
        return tot / float(window_s)


def rss_mb() -> float | None:
    """Resident memory of this process in MB (peak RSS where only that is available)."""
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)
    except OSError:
        pass
    try:
        import resource

        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / (1024.0 * 1024.0) if sys.platform == "darwin" else r / 1024.0, 1)
    except ImportError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024.0 * 1024.0), 1)
        except Exception:
            return None
    return None


class Metrics:
    def __init__(self, max_ms: float = 2000.0):
        self.hist = {s: LatencyHistogram(max_ms) for s in STAGES}
        self.tp = Throughput()
        self.decisions = {"allow": 0, "verify": 0, "hold": 0}
        self.warmup_events = 0
        self.p95_series: deque = deque(maxlen=120)
        self._last_series = 0.0

    def record(self, timings_ns: dict, decision: str) -> None:
        for s in STAGES:
            self.hist[s].add(timings_ns[s] / 1000.0)
        self.decisions[decision] += 1
        self.tp.add()

    def snapshot(self) -> dict:
        now = time.monotonic()
        lat = {s: self.hist[s].percentiles() for s in STAGES}
        if now - self._last_series >= 1.0 and lat["total"]["p95"] is not None:
            self.p95_series.append(lat["total"]["p95"])
            self._last_series = now
        return {
            "latency_us": lat,
            "throughput": {
                "events_per_s_10s": round(self.tp.rate(10, now), 1),
                "events_per_s_60s": round(self.tp.rate(60, now), 1),
                "total_events": self.tp.total,
            },
            "decisions": dict(self.decisions),
            "warmup_events": self.warmup_events,
            "memory": {"rss_mb": rss_mb()},
            "p95_series_us": list(self.p95_series),
        }
