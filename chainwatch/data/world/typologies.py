"""Mule-ring campaigns (spec 8.2.3): six typologies planned as victim and forward intents.

Amounts of forwards are fractions of the mule's campaign pool and are resolved in the balance pass, so money
is conserved per ring. Every campaign transaction carries ``camp`` (ring id), ``hop`` and roles.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from chainwatch.data.world.balances import (
    CASH_IN,
    CASH_OUT,
    FIXED,
    FORWARD,
    PAYMENT,
    TRANSFER,
    VICTIM,
    Intent,
)
from chainwatch.data.world.population import DAY_S, HOUR_WEIGHTS, Population, lognormal

TYPOLOGIES: tuple[str, ...] = (
    "funnel",
    "layered_chain",
    "split_merge",
    "round_trip",
    "structuring",
    "slow_roll",
)
EVENING = np.array([1.5 if 18 <= h <= 23 else 1.0 for h in range(24)])
START_W = HOUR_WEIGHTS * EVENING
START_W = START_W / START_W.max()


@dataclass
class RingPlan:
    ring_id: int
    typology: str
    start: float
    victims: list[int] = field(default_factory=list)
    mules: list[int] = field(default_factory=list)
    collectors: list[int] = field(default_factory=list)
    sink: int = -1
    sink_type: str = "CASH_OUT"
    end: float = 0.0
    reused: list[int] = field(default_factory=list)


class _Campaign:
    """Collects the intents of one campaign and the planned credit times per account."""

    def __init__(
        self,
        camp: int,
        rng: np.random.Generator,
        delay_scale: float,
        structuring_cap: float,
        structuring: bool,
    ):
        self.camp = camp
        self.rng = rng
        self.delay_scale = delay_scale
        self.intents: list[Intent] = []
        self.credits: dict[int, list[float]] = defaultdict(list)
        self.cap = structuring_cap if structuring else 0.0

    def delay(self, median_s: float, sigma: float) -> float:
        return float(median_s * np.exp(sigma * self.rng.standard_normal()) * self.delay_scale)

    def victim(self, t: float, v: int, m: int, amount: float, dst_role: str = "mule") -> None:
        self.intents.append(
            Intent(
                t,
                VICTIM,
                v,
                m,
                TRANSFER,
                amount=amount,
                credit_pool=(self.camp, m),
                camp=self.camp,
                hop=0,
                src_role="victim",
                dst_role=dst_role,
            )
        )
        self.credits[m].append(round(t, 3))

    def fwd(
        self,
        t: float,
        src: int,
        dst: int,
        frac: float,
        hop: int,
        sr: str,
        dr: str,
        typ: int = TRANSFER,
        terminal: bool = False,
        round100: bool = False,
    ) -> None:
        self.intents.append(
            Intent(
                t,
                FORWARD,
                src,
                dst,
                typ,
                fraction=frac,
                src_pool=(self.camp, src),
                credit_pool=None if terminal else (self.camp, dst),
                camp=self.camp,
                hop=hop,
                src_role=sr,
                dst_role=dr,
                cap=self.cap,
                round100=round100,
            )
        )
        if not terminal:
            self.credits[dst].append(round(t, 3))

    def forward_times(
        self, credits: list[float], batching: bool, median_s: float, sigma: float
    ) -> list[float]:
        """Per-credit forwards, or batches of 2 to 6 credits (or 20 minutes) forwarded once."""
        credits = sorted(credits)
        if not batching:
            return [t + self.delay(median_s, sigma) for t in credits]
        out, i = [], 0
        while i < len(credits):
            k = int(self.rng.integers(2, 7))
            open_t = credits[i]
            j = i
            while j + 1 < len(credits) and (j - i + 1) < k and credits[j + 1] <= open_t + 1200.0:
                j += 1
            close = credits[j] if (j - i + 1) == k else max(credits[j], open_t + 1200.0)
            out.append(close + self.delay(median_s, sigma))
            i = j + 1
        return out

    def chunks(
        self,
        t: float,
        src: int,
        dst: int,
        n: int,
        gap: tuple[float, float],
        hop: int,
        sr: str,
        dr: str,
        typ: int,
        terminal: bool,
        skim: tuple[float, float] = (0.90, 0.99),
        round100: bool = False,
    ) -> None:
        for i in range(n):
            frac = 1.0 / (n - i) if i < n - 1 else float(self.rng.uniform(*skim))
            self.fwd(t, src, dst, frac, hop, sr, dr, typ=typ, terminal=terminal, round100=round100)
            t += float(self.rng.uniform(*gap))


def group_times(times: list[float], gap_s: float) -> list[float]:
    """End time of each group of credits whose consecutive gaps are at most ``gap_s``."""
    times = sorted(times)
    if not times:
        return []
    ends, last = [], times[0]
    for t in times[1:]:
        if t - last > gap_s:
            ends.append(last)
        last = t
    ends.append(last)
    return ends


def plan_campaigns(cfg, pop: Population, rng: np.random.Generator):
    """Plan every campaign. Returns ``(intents, ring_plans, extra_bg)``.

    ``extra_bg`` lists ``(account, from_ts, to_ts)`` windows of ordinary history for dormant mule accounts.
    """
    w = cfg.world
    days = float(w.days)
    tz = float(cfg.project.tz_offset_s)
    n_camp = int(w.n_campaigns)
    mix = {k: float(v) for k, v in w.mix.items()}
    names = [t for t in TYPOLOGIES if mix.get(t, 0.0) > 0]
    probs = np.array([mix[t] for t in names])
    probs = probs / probs.sum()
    forced = list(w.get("force_typologies", []) or [])
    mule_mix = {k: float(v) for k, v in w.mule_mix.items()}
    mm_names = [k for k in ("new", "dormant", "active") if mule_mix.get(k, 0.0) > 0]
    mm_p = np.array([mule_mix[k] for k in mm_names])
    mm_p = mm_p / mm_p.sum()
    delay_scale = float(w.delay_scale)
    s_cap = float(w.structuring_cap_inr)
    cap = float(w.upi_txn_cap_inr)
    lo_day = float(w.campaign_start_min_day)
    margin = float(w.campaign_end_margin_days)

    a_start, n_agents = pop.start("agent"), pop.count("agent")
    m_start, n_m = pop.start("merchant"), pop.count("merchant")
    pool_a, pool_b = pop.ranges["mule_pool"]
    next_pool = pool_a
    business = set(pop.business_clients.tolist())
    regular = pop.regular_pool
    victim_pool = np.array([i for i in regular.tolist() if pop.balance0[i] >= 3000.0 and i not in business])
    active_pool = np.array([i for i in regular.tolist() if i not in business])

    # schedule
    schedule = []
    for c in range(n_camp):
        typ = forced[c % len(forced)] if forced else str(rng.choice(names, p=probs))
        hi_day = days - margin - (1.5 if typ == "slow_roll" else 0.0)
        hi_day = max(hi_day, lo_day + 0.1)
        while True:
            start = rng.uniform(lo_day, hi_day) * DAY_S
            hour = int(((start + tz) % DAY_S) // 3600)
            if rng.random() < START_W[hour]:
                break
        schedule.append((start, typ))
    order = sorted(range(n_camp), key=lambda c: (schedule[c][0], c))

    intents: list[Intent] = []
    plans: list[RingPlan] = []
    extra_bg: list[tuple[int, float, float]] = []
    mule_uses: dict[int, int] = {}
    mule_last_end: dict[int, float] = {}

    for ring_id, c in enumerate(order):
        start, typ = schedule[c]
        plan = RingPlan(ring_id=ring_id, typology=typ, start=start)
        cp = _Campaign(ring_id, rng, delay_scale, s_cap, structuring=(typ == "structuring"))
        fresh: dict[int, str] = {}

        # victims and arrivals
        nv = min(60, 3 + int(rng.geometric(0.12)) - 1)
        if typ == "layered_chain":
            nv = min(nv, 15)
        nv = min(nv, victim_pool.size)
        victims = rng.choice(victim_pool, size=nv, replace=False).tolist()
        if typ == "slow_roll":
            span = min(float(lognormal(rng, 8 * 3600.0, 0.7)), 2 * DAY_S)
        else:
            span = min(float(lognormal(rng, 5400.0, 0.7)), 8 * 3600.0)
        arrivals = sorted((start + rng.beta(1.2, 2.5, size=nv) * span).tolist())
        vic_amounts = lognormal(rng, 9000.0, 0.9, nv)
        taken = set(victims)

        def pick_mule(role_list: list[int]) -> int:
            nonlocal next_pool
            if rng.random() < 0.25:
                eligible = sorted(
                    m
                    for m, u in mule_uses.items()
                    if u < 3 and mule_last_end[m] + 2 * DAY_S <= start and m not in taken
                )
                if eligible:
                    m = int(eligible[int(rng.integers(0, len(eligible)))])
                    plan.reused.append(m)
                    taken.add(m)
                    role_list.append(m)
                    return m
            kind = str(rng.choice(mm_names, p=mm_p))
            if kind in ("new", "dormant") and next_pool < pool_b:
                m = next_pool
                next_pool += 1
            else:
                kind = "active"
                while True:
                    m = int(active_pool[rng.integers(0, active_pool.size)])
                    if m not in taken and m not in mule_uses:
                        break
            fresh[m] = kind
            taken.add(m)
            role_list.append(m)
            return m

        if rng.random() < 0.85:
            sink = a_start + int(rng.choice(n_agents, p=pop.agent_p))
            sink_t, sink_name, r100 = CASH_OUT, "CASH_OUT", True
        else:
            sink = m_start + int(rng.choice(n_m, p=pop.merchant_p))
            sink_t, sink_name, r100 = PAYMENT, "PAYMENT", False
        plan.sink, plan.sink_type = sink, sink_name
        slow = typ == "slow_roll"

        def fwd_med(fast_s: float) -> tuple[float, float]:
            return (3 * 3600.0, 0.6) if slow else (fast_s, 0.7)

        def l1_forwards(
            src: int, dst: int, hop: int, sr: str, dr: str, med: float, sig: float, frac_rng=(0.90, 0.99)
        ) -> None:
            times = cp.forward_times(cp.credits[src], bool(rng.random() < 0.5), med, sig)
            for t in times:
                if slow:
                    cp.chunks(
                        t,
                        src,
                        dst,
                        int(rng.integers(3, 6)),
                        (1200.0, 5400.0),
                        hop,
                        sr,
                        dr,
                        TRANSFER,
                        False,
                        skim=frac_rng,
                    )
                else:
                    cp.fwd(t, src, dst, float(rng.uniform(*frac_rng)), hop, sr, dr)

        def cash_out(src: int, hop: int, sr: str, med: float, sig: float, n_rng=(1, 4)) -> None:
            for t_end in group_times(cp.credits[src], 1200.0 if not slow else 4 * 3600.0):
                t = t_end + cp.delay(med, sig)
                if slow:
                    cp.chunks(
                        t,
                        src,
                        sink,
                        int(rng.integers(3, 6)),
                        (1200.0, 5400.0),
                        hop,
                        sr,
                        "sink",
                        sink_t,
                        True,
                        round100=r100,
                    )
                else:
                    cp.chunks(
                        t,
                        src,
                        sink,
                        int(rng.integers(*n_rng)),
                        (60.0, 300.0),
                        hop,
                        sr,
                        "sink",
                        sink_t,
                        True,
                        round100=r100,
                    )

        if typ in ("funnel", "structuring", "slow_roll"):
            l1 = [pick_mule(plan.mules) for _ in range(int(rng.integers(1, 5)))]
            cols = [pick_mule(plan.collectors) for _ in range(int(rng.integers(1, 3)))]
            wts = rng.dirichlet(np.ones(len(l1)))
            assign = rng.choice(len(l1), size=nv, p=wts)
            for t, v, a, amt in zip(arrivals, victims, assign, vic_amounts, strict=False):
                cp.victim(t, int(v), l1[int(a)], float(amt))
            for j, m in enumerate(l1):
                med, sig = fwd_med(360.0)
                l1_forwards(m, cols[j % len(cols)], 1, "mule", "collector", med, sig)
            for col in cols:
                med, sig = fwd_med(480.0)
                cash_out(col, 2, "collector", med, sig)

        elif typ == "layered_chain":
            chain = [pick_mule(plan.mules) for _ in range(int(rng.integers(3, 7)))]
            for t, v, amt in zip(arrivals, victims, vic_amounts, strict=False):
                cp.victim(t, int(v), chain[0], float(amt))
            for i, m in enumerate(chain):
                times = cp.forward_times(cp.credits[m], bool(rng.random() < 0.5), 240.0, 0.6)
                if i + 1 < len(chain):
                    for t in times:
                        cp.fwd(
                            t, m, chain[i + 1], 1.0 - float(rng.uniform(0.01, 0.04)), i + 1, "mule", "mule"
                        )
                else:
                    for t in times:
                        cp.chunks(
                            t,
                            m,
                            sink,
                            int(rng.integers(1, 3)),
                            (60.0, 300.0),
                            i + 1,
                            "mule",
                            "sink",
                            sink_t,
                            True,
                            skim=(0.96, 0.99),
                            round100=r100,
                        )

        elif typ == "split_merge":
            l1 = [pick_mule(plan.mules) for _ in range(int(rng.integers(2, 5)))]
            l2 = {m: [pick_mule(plan.mules) for _ in range(int(rng.integers(2, 4)))] for m in l1}
            col = pick_mule(plan.collectors)
            wts = rng.dirichlet(np.ones(len(l1)))
            assign = rng.choice(len(l1), size=nv, p=wts)
            for t, v, a, amt in zip(arrivals, victims, assign, vic_amounts, strict=False):
                cp.victim(t, int(v), l1[int(a)], float(amt))
            for m in l1:
                kids = l2[m]
                for t in cp.forward_times(cp.credits[m], bool(rng.random() < 0.5), 300.0, 0.7):
                    for ci, kid in enumerate(kids):
                        frac = (
                            1.0 / (len(kids) - ci) if ci < len(kids) - 1 else float(rng.uniform(0.96, 0.99))
                        )
                        cp.fwd(t + ci * float(rng.uniform(30.0, 120.0)), m, kid, frac, 1, "mule", "mule")
            for m in l1:
                for kid in l2[m]:
                    for t in cp.forward_times(cp.credits[kid], bool(rng.random() < 0.5), 300.0, 0.7):
                        cp.fwd(t, kid, col, float(rng.uniform(0.95, 0.99)), 2, "mule", "collector")
            cash_out(col, 3, "collector", 300.0, 0.7)

        elif typ == "round_trip":
            a = pick_mule(plan.mules)
            b = pick_mule(plan.mules)
            c3 = pick_mule(plan.mules)
            for t, v, amt in zip(arrivals, victims, vic_amounts, strict=False):
                cp.victim(t, int(v), a, float(amt))
            t = arrivals[-1] + cp.delay(420.0, 0.7)
            hop = 1
            for _ in range(int(rng.integers(1, 3))):
                cp.fwd(t, a, b, float(rng.uniform(0.4, 0.7)), hop, "mule", "mule")
                t += cp.delay(420.0, 0.7)
                cp.fwd(t, b, c3, float(rng.uniform(0.95, 0.99)), hop + 1, "mule", "mule")
                t += cp.delay(420.0, 0.7)
                cp.fwd(t, c3, a, float(rng.uniform(0.95, 0.99)), hop + 2, "mule", "mule")
                t += cp.delay(420.0, 0.7)
                hop += 3
            cp.chunks(
                t,
                a,
                sink,
                int(rng.integers(1, 3)),
                (60.0, 300.0),
                hop,
                "mule",
                "sink",
                sink_t,
                True,
                round100=r100,
            )
        else:  # pragma: no cover
            raise ValueError(f"unknown typology {typ}")

        plan.victims = [int(v) for v in victims]
        plan.end = max(it.ts for it in cp.intents)

        # account bookkeeping: mule profiles, first-seen events, dormant history
        for m in plan.mules + plan.collectors:
            mule_uses[m] = mule_uses.get(m, 0) + 1
            mule_last_end[m] = plan.end
            pop.is_mule[m] = 1
            pop.used[m] = True
            if pop.first_ring[m] < 0:
                pop.first_ring[m] = ring_id
            kind = fresh.get(m)
            if kind is None:
                continue
            pop.mule_kind[m] = kind
            t_first = min(cp.credits[m]) if cp.credits[m] else start
            if kind == "active":
                pop.is_unwitting[m] = 1
            elif kind == "dormant":
                pop.is_unwitting[m] = 1 if rng.random() < 0.5 else 0
                until = t_first - 14 * DAY_S - float(rng.uniform(0.0, 5 * DAY_S))
                pop.balance0[m] = float(lognormal(rng, 3000.0, 1.0))
                if until > 0:
                    extra_bg.append((m, 0.0, until))
            else:  # new: first seen 0 to 6 h before its first credit
                t_seen = max(0.0, t_first - float(rng.uniform(0.0, 6 * 3600.0)))
                agent = a_start + int(rng.choice(n_agents, p=pop.agent_p))
                amount = float(np.clip(lognormal(rng, 400.0, 0.6), 100.0, 2000.0))
                intents.append(Intent(t_seen, FIXED, agent, m, CASH_IN, amount=round(min(amount, cap), 2)))
        intents.extend(cp.intents)
        plans.append(plan)

    return intents, plans, extra_bg
