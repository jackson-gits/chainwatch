"""Pure-Python mini campaign generator for live injection (spec 13.2). No numpy.

Follows the typology structures and timing laws of spec 8.2.3 with ``random.Random.lognormvariate``;
every delay (and the victim arrival span) is multiplied by ``time_scale`` so a demo ring unfolds in
minutes. Balances are unknown (null) for injected events.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

from chainwatch.schema import Event, LabeledEvent

TYPOLOGIES = ("funnel", "layered_chain", "split_merge", "round_trip", "structuring", "slow_roll")
STRUCTURING_CAP = 49999.0


def _ln(rng: random.Random, median: float, sigma: float) -> float:
    return rng.lognormvariate(math.log(median), sigma)


class _Plan:
    def __init__(self, rng: random.Random, scale: float, ring_id: str, structuring: bool):
        self.rng = rng
        self.scale = scale
        self.ring_id = ring_id
        self.items: list[tuple] = []  # (ts, seq, kind, src, dst, value, hop, sr, dr, typ)
        self.credits: dict[str, list[float]] = {}
        self.seq = 0
        self.structuring = structuring

    def delay(self, median: float, sigma: float) -> float:
        return _ln(self.rng, median, sigma) * self.scale

    def add(self, ts, kind, src, dst, value, hop, sr, dr, typ="TRANSFER", credit=True):
        self.items.append((ts, self.seq, kind, src, dst, value, hop, sr, dr, typ))
        self.seq += 1
        if credit:
            self.credits.setdefault(dst, []).append(ts)

    def forward_times(self, acct: str, median: float, sigma: float) -> list[float]:
        credits = sorted(self.credits.get(acct, []))
        if self.rng.random() >= 0.5:
            return [t + self.delay(median, sigma) for t in credits]
        out, i = [], 0
        while i < len(credits):
            k = self.rng.randint(2, 6)
            open_t = credits[i]
            j = i
            while j + 1 < len(credits) and (j - i + 1) < k and credits[j + 1] <= open_t + 1200 * self.scale:
                j += 1
            close = credits[j] if (j - i + 1) == k else max(credits[j], open_t + 1200 * self.scale)
            out.append(close + self.delay(median, sigma))
            i = j + 1
        return out

    def chunks(self, t, src, dst, n, gap, hop, sr, dr, typ, credit, skim=(0.90, 0.99)):
        for i in range(n):
            frac = 1.0 / (n - i) if i < n - 1 else self.rng.uniform(*skim)
            self.add(t, "fwd", src, dst, frac, hop, sr, dr, typ, credit)
            t += self.rng.uniform(*gap) * self.scale


def make_campaign(
    typology: str,
    rng: random.Random,
    start_ts: float,
    victims: list[str],
    new_mule: Callable[[], str],
    sink: str,
    sink_type: str = "CASH_OUT",
    time_scale: float = 0.5,
    ring_id: str = "INJ1",
    first_event_id: int = 10_000_000,
) -> list[LabeledEvent]:
    """Plan and resolve one campaign. Returns labeled events sorted by (ts, event_id)."""
    if typology not in TYPOLOGIES:
        raise ValueError(f"unknown typology {typology!r}")
    slow = typology == "slow_roll"
    p = _Plan(rng, time_scale, ring_id, typology == "structuring")
    nv = len(victims)
    span = min(_ln(rng, 8 * 3600.0, 0.7), 2 * 86400.0) if slow else min(_ln(rng, 5400.0, 0.7), 8 * 3600.0)
    span *= time_scale
    arrivals = sorted(start_ts + rng.betavariate(1.2, 2.5) * span for _ in range(nv))
    amounts = [min(max(_ln(rng, 9000.0, 0.9), 500.0), 40000.0) for _ in range(nv)]
    med_fwd = (3 * 3600.0, 0.6) if slow else (360.0, 0.7)
    med_col = (3 * 3600.0, 0.6) if slow else (480.0, 0.7)

    def l1_out(src, dst, hop, sr, dr):
        for t in p.forward_times(src, *med_fwd):
            if slow:
                p.chunks(t, src, dst, rng.randint(3, 5), (1200.0, 5400.0), hop, sr, dr, "TRANSFER", True)
            else:
                p.add(t, "fwd", src, dst, rng.uniform(0.90, 0.99), hop, sr, dr)

    def cash_out(src, hop, sr, med):
        times = sorted(p.credits.get(src, []))
        gap = (4 * 3600.0 if slow else 1200.0) * time_scale
        ends, last = [], None
        for t in times:
            if last is not None and t - last > gap:
                ends.append(last)
            last = t
        if last is not None:
            ends.append(last)
        for te in ends:
            t = te + p.delay(*med)
            n = rng.randint(3, 5) if slow else rng.randint(1, 3)
            g = (1200.0, 5400.0) if slow else (60.0, 300.0)
            p.chunks(t, src, sink, n, g, hop, sr, "sink", sink_type, False)

    if typology in ("funnel", "structuring", "slow_roll"):
        l1 = [new_mule() for _ in range(rng.randint(1, 4))]
        cols = [new_mule() for _ in range(rng.randint(1, 2))]
        w = [rng.gammavariate(1.0, 1.0) for _ in l1]
        for t, v, a in zip(arrivals, victims, amounts, strict=False):
            m = rng.choices(l1, weights=w)[0]
            p.add(t, "victim", v, m, a, 0, "victim", "mule")
        for j, m in enumerate(l1):
            l1_out(m, cols[j % len(cols)], 1, "mule", "collector")
        for c in cols:
            cash_out(c, 2, "collector", med_col)
    elif typology == "layered_chain":
        chain = [new_mule() for _ in range(rng.randint(3, 6))]
        for t, v, a in zip(arrivals, victims, amounts, strict=False):
            p.add(t, "victim", v, chain[0], a, 0, "victim", "mule")
        for i, m in enumerate(chain):
            for t in p.forward_times(m, 240.0, 0.6):
                if i + 1 < len(chain):
                    p.add(t, "fwd", m, chain[i + 1], 1.0 - rng.uniform(0.01, 0.04), i + 1, "mule", "mule")
                else:
                    p.chunks(
                        t,
                        m,
                        sink,
                        rng.randint(1, 2),
                        (60.0, 300.0),
                        i + 1,
                        "mule",
                        "sink",
                        sink_type,
                        False,
                        skim=(0.96, 0.99),
                    )
    elif typology == "split_merge":
        l1 = [new_mule() for _ in range(rng.randint(2, 4))]
        l2 = {m: [new_mule() for _ in range(rng.randint(2, 3))] for m in l1}
        col = new_mule()
        for t, v, a in zip(arrivals, victims, amounts, strict=False):
            p.add(t, "victim", v, rng.choice(l1), a, 0, "victim", "mule")
        for m in l1:
            kids = l2[m]
            for t in p.forward_times(m, 300.0, 0.7):
                for ci, kid in enumerate(kids):
                    frac = 1.0 / (len(kids) - ci) if ci < len(kids) - 1 else rng.uniform(0.96, 0.99)
                    p.add(
                        t + ci * rng.uniform(30.0, 120.0) * time_scale, "fwd", m, kid, frac, 1, "mule", "mule"
                    )
        for m in l1:
            for kid in l2[m]:
                for t in p.forward_times(kid, 300.0, 0.7):
                    p.add(t, "fwd", kid, col, rng.uniform(0.95, 0.99), 2, "mule", "collector")
        cash_out(col, 3, "collector", (300.0, 0.7))
    else:  # round_trip
        a, b, c = new_mule(), new_mule(), new_mule()
        for t, v, amt in zip(arrivals, victims, amounts, strict=False):
            p.add(t, "victim", v, a, amt, 0, "victim", "mule")
        t = arrivals[-1] + p.delay(420.0, 0.7)
        hop = 1
        for _ in range(rng.randint(1, 2)):
            p.add(t, "fwd", a, b, rng.uniform(0.4, 0.7), hop, "mule", "mule")
            t += p.delay(420.0, 0.7)
            p.add(t, "fwd", b, c, rng.uniform(0.95, 0.99), hop + 1, "mule", "mule")
            t += p.delay(420.0, 0.7)
            p.add(t, "fwd", c, a, rng.uniform(0.95, 0.99), hop + 2, "mule", "mule")
            t += p.delay(420.0, 0.7)
            hop += 3
        p.chunks(t, a, sink, rng.randint(1, 2), (60.0, 300.0), hop, "mule", "sink", sink_type, False)

    # resolve amounts in time order with per-account campaign pools (money is conserved)
    pools: dict[str, float] = {}
    out: list[LabeledEvent] = []
    queue = sorted(p.items, key=lambda it: (it[0], it[1]))
    k = 0
    extra_seq = len(queue)
    while k < len(queue):
        ts, _seq, kind, src, dst, value, hop, sr, dr, typ = queue[k]
        k += 1
        if kind == "victim":
            amount = round(value, 2)
        else:
            amount = (
                round(value * pools.get(src, 0.0), 2)
                if kind == "fwd"
                else round(min(value, pools.get(src, 0.0)), 2)
            )
            if amount < 1.0:
                continue
            if p.structuring and amount > STRUCTURING_CAP:
                chunk = round(rng.uniform(0.85, 0.99) * STRUCTURING_CAP, 2)
                rest = (
                    ts + rng.uniform(60.0, 300.0) * time_scale,
                    extra_seq,
                    "abs",
                    src,
                    dst,
                    amount - chunk,
                    hop,
                    sr,
                    dr,
                    typ,
                )
                extra_seq += 1
                queue.append(rest)
                queue[k:] = sorted(queue[k:], key=lambda it: (it[0], it[1]))
                amount = chunk
            pools[src] = pools.get(src, 0.0) - amount
        if dr != "sink":
            pools[dst] = pools.get(dst, 0.0) + amount
        ev = Event(first_event_id + len(out), round(ts, 3), src, dst, amount, typ)
        out.append(LabeledEvent(ev, 1, ring_id, hop, sr, dr))
    return out
