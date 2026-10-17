"""Intents and the single sequential balance pass (spec 8.2.4 step 5).

Planned activity is expressed as *intents*:

* plain fixed intents (the bulk of background traffic) live in numpy arrays;
* special intents (campaign flows, forwards from pools, split bills, charity, merchant payouts) are ``Intent``
  objects.

The pass walks all intents in ``(ts, seq)`` order, resolves amounts against balances and money pools, and
yields final event rows. Forward intents move a *fraction of a pool*; a pool is money that arrived for a
purpose (a mule's campaign money, a merchant's daily takings, a split-bill collection). Credits increase the
receiver's pool, so money is conserved: no mule can forward more than it received for that campaign.
"""

from __future__ import annotations

import heapq
import math
import random
from collections.abc import Iterator

import numpy as np

TRANSFER, PAYMENT, CASH_OUT, CASH_IN, DEBIT = 0, 1, 2, 3, 4
TYPE_NAMES = ("TRANSFER", "PAYMENT", "CASH_OUT", "CASH_IN", "DEBIT")

FIXED, VICTIM, FORWARD, ABS_FORWARD = 0, 1, 2, 3


class Intent:
    """A planned transaction whose final amount is resolved in the balance pass."""

    __slots__ = (
        "ts",
        "seq",
        "kind",
        "src",
        "dst",
        "typ",
        "amount",
        "fraction",
        "src_pool",
        "credit_pool",
        "camp",
        "hop",
        "src_role",
        "dst_role",
        "cap",
        "round100",
        "drain",
    )

    def __init__(
        self,
        ts,
        kind,
        src,
        dst,
        typ,
        amount=0.0,
        fraction=0.0,
        src_pool=None,
        credit_pool=None,
        camp=-1,
        hop=-1,
        src_role="legit",
        dst_role="legit",
        cap=0.0,
        round100=False,
        drain=False,
    ):
        self.ts = round(float(ts), 3)
        self.seq = 0
        self.kind = kind
        self.src = int(src)
        self.dst = int(dst)
        self.typ = typ
        self.amount = float(amount)
        self.fraction = float(fraction)
        self.src_pool = src_pool
        self.credit_pool = credit_pool
        self.camp = camp
        self.hop = hop
        self.src_role = src_role
        self.dst_role = dst_role
        self.cap = float(cap)
        self.round100 = round100
        self.drain = drain


class PlainIntents:
    """Column store for the bulk of fixed background intents."""

    def __init__(self):
        self.parts: list[tuple[np.ndarray, ...]] = []

    def add(self, ts, src, dst, typ, amount) -> None:
        ts = np.asarray(ts, dtype=float)
        if ts.size == 0:
            return
        n = ts.size
        self.parts.append(
            (
                np.round(ts, 3),
                np.asarray(src, dtype=np.int64).reshape(-1) * np.ones(n, dtype=np.int64),
                np.asarray(dst, dtype=np.int64).reshape(-1) * np.ones(n, dtype=np.int64),
                np.asarray(typ, dtype=np.int64).reshape(-1) * np.ones(n, dtype=np.int64),
                np.round(np.asarray(amount, dtype=float), 2),
            )
        )

    def arrays(self) -> tuple[np.ndarray, ...]:
        if not self.parts:
            e = np.zeros(0)
            return e, e.astype(np.int64), e.astype(np.int64), e.astype(np.int64), e
        return tuple(np.concatenate([p[k] for p in self.parts]) for k in range(5))


def balance_pass(
    pop,
    plain: PlainIntents,
    specials: list[Intent],
    end_ts: float,
    upi_cap: float,
    structuring_cap: float,
    seed: int,
) -> Iterator[tuple]:
    """Resolve intents in time order and yield event rows.

    Yields ``(ts, src, dst, typ, amount, sbb, sba, dbb, dba, camp, hop, src_role, dst_role)`` with integer
    account indices and type codes; balances are ``None`` for untracked accounts.
    """
    rnd = random.Random(seed)
    ts_p, src_p, dst_p, typ_p, amt_p = plain.arrays()
    n_plain = ts_p.size
    for k, it in enumerate(specials):
        it.seq = n_plain + k
    ts_all = np.concatenate([ts_p, np.array([it.ts for it in specials], dtype=float)])
    order = np.lexsort((np.arange(ts_all.size), ts_all)).tolist()
    ts_l, src_l, dst_l, typ_l, amt_l = (
        ts_p.tolist(),
        src_p.tolist(),
        dst_p.tolist(),
        typ_p.tolist(),
        amt_p.tolist(),
    )
    seq_next = ts_all.size

    tracked = pop.tracked.tolist()
    bal = [round(float(b), 2) for b in pop.balance0.tolist()]
    n_customers = pop.count("customer")
    m_a, m_b = pop.ranges["merchant"]
    t_a, t_b = pop.ranges["trader"]
    home_agent = pop.home_agent.tolist()
    pools: dict = {}
    pending_topup: dict[int, float] = {}
    heap: list = []

    def topup(acct: int, ts: float) -> None:
        nonlocal seq_next
        if acct >= n_customers or pending_topup.get(acct, -1.0) > ts:
            return
        if rnd.random() >= 0.5:
            return
        delay = min(max(21600.0 * math.exp(1.0 * rnd.gauss(0.0, 1.0)), 600.0), 3 * 86400.0)
        amount = min(max(3000.0 * math.exp(0.8 * rnd.gauss(0.0, 1.0)), 100.0), upi_cap)
        if rnd.random() < 0.7:
            amount = max(100.0, round(amount / 100.0) * 100.0)
        it = Intent(ts + delay, FIXED, home_agent[acct], acct, CASH_IN, amount=amount)
        it.seq = seq_next
        seq_next += 1
        pending_topup[acct] = it.ts
        heapq.heappush(heap, (it.ts, it.seq, it))

    def auto_pool(dst: int):
        if m_a <= dst < m_b or t_a <= dst < t_b:
            return ("b", dst)
        return None

    def execute(ts, src, dst, typ, amount, credit_pool, camp, hop, sr, dr):
        if tracked[src]:
            sbb = bal[src]
            sba = round(sbb - amount, 2)
            bal[src] = sba
        else:
            sbb = sba = None
        if tracked[dst]:
            dbb = bal[dst]
            dba = round(dbb + amount, 2)
            bal[dst] = dba
        else:
            dbb = dba = None
        key = credit_pool if credit_pool is not None else auto_pool(dst)
        if key is not None:
            pools[key] = pools.get(key, 0.0) + amount
        return (ts, src, dst, typ, amount, sbb, sba, dbb, dba, camp, hop, sr, dr)

    def resolve_fixed(ts, src, dst, typ, amount):
        if tracked[src]:
            b = bal[src]
            if b < amount:
                topup(src, ts)
                if b < 100.0:
                    return None
                amount = round(b * rnd.uniform(0.5, 1.0), 2)
                if amount < 1.0:
                    return None
        return amount

    def run_special(it: Intent):
        nonlocal seq_next
        ts, src, dst = it.ts, it.src, it.dst
        if it.kind == FIXED:
            amount = resolve_fixed(ts, src, dst, it.typ, it.amount)
            if amount is None:
                return None
            row = execute(
                ts, src, dst, it.typ, amount, it.credit_pool, it.camp, it.hop, it.src_role, it.dst_role
            )
            if tracked[src] and bal[src] < 500.0:
                topup(src, ts)
            return row
        if it.kind == VICTIM:
            b = bal[src] if tracked[src] else it.amount
            amount = min(it.amount, 0.9 * b, upi_cap)
            if amount < 100.0:
                return None
            amount = round(max(amount, min(500.0, 0.9 * b)), 2)
            amount = min(amount, b)
            return execute(
                ts, src, dst, it.typ, amount, it.credit_pool, it.camp, it.hop, it.src_role, it.dst_role
            )
        # forwards out of a pool
        pool = pools.get(it.src_pool, 0.0)
        if it.kind == FORWARD:
            amount = round(it.fraction * pool, 2)
        else:  # ABS_FORWARD: a remaining absolute amount, limited by the pool
            amount = round(min(it.amount, pool), 2)
        if tracked[src]:
            amount = min(amount, bal[src])
        if amount < 1.0:
            return None
        drain_now = it.drain
        if it.cap > 0 and amount > it.cap:
            if it.camp >= 0:  # structuring: chunk below the cap, re-queue the rest
                chunk = round(rnd.uniform(0.85, 0.99) * structuring_cap, 2)
            else:
                chunk = round(it.cap, 2)
            chunk = min(chunk, amount)
            rest = Intent(
                ts + rnd.uniform(60.0, 300.0),
                ABS_FORWARD,
                src,
                dst,
                it.typ,
                amount=amount - chunk,
                src_pool=it.src_pool,
                credit_pool=it.credit_pool,
                camp=it.camp,
                hop=it.hop,
                src_role=it.src_role,
                dst_role=it.dst_role,
                cap=it.cap,
                round100=it.round100,
                drain=it.drain,
            )
            rest.seq = seq_next
            seq_next += 1
            heapq.heappush(heap, (rest.ts, rest.seq, rest))
            amount = chunk
            drain_now = False  # only the last chunk drains
        if it.round100 and amount >= 1000.0 and rnd.random() < 0.6:
            amount = math.floor(amount / 100.0) * 100.0
        # drain: the part not forwarded becomes the owner's own money (traders, merchants, family hubs)
        pools[it.src_pool] = 0.0 if drain_now else pool - amount
        return execute(
            ts, src, dst, it.typ, amount, it.credit_pool, it.camp, it.hop, it.src_role, it.dst_role
        )

    def run_heap_until(ts_lim, seq_lim):
        while heap and (heap[0][0], heap[0][1]) < (ts_lim, seq_lim):
            _, _, it = heapq.heappop(heap)
            if it.ts >= end_ts:
                continue
            row = run_special(it)
            if row is not None:
                yield row

    for k in order:
        if k < n_plain:
            ts = ts_l[k]
            if heap:
                yield from run_heap_until(ts, k)
            if ts < 0.0 or ts >= end_ts:
                continue
            src, dst, typ = src_l[k], dst_l[k], typ_l[k]
            amount = resolve_fixed(ts, src, dst, typ, amt_l[k])
            if amount is None:
                continue
            row = execute(ts, src, dst, typ, amount, None, -1, -1, "legit", "legit")
            if tracked[src] and bal[src] < 500.0:
                topup(src, ts)
            yield row
        else:
            it = specials[k - n_plain]
            if heap:
                yield from run_heap_until(it.ts, it.seq)
            if it.ts < 0.0 or it.ts >= end_ts:
                continue
            row = run_special(it)
            if row is not None:
                yield row
    yield from run_heap_until(math.inf, 0)
