"""Per-account state for the feature engine (spec 9.4) and one-pass window scans. Pure Python.

Deque entries (appended in time order, so a window is a suffix):

* ``out``: ``(ts, dst, amount, new_payee, is_round)`` per debit
* ``inc``: ``(ts, src, amount, first_contact)`` per credit
* ``credits``: ``[ts, remaining, src, depth, origins]``, the FIFO of unspent credits
"""

from __future__ import annotations

from collections import OrderedDict, deque


class AccountState:
    """Heavy, evictable state of one account."""

    __slots__ = ("out", "inc", "credits", "cp", "amt_hist", "dwell_ewma")

    def __init__(self, deque_cap: int, amount_history_n: int, dwell_default: float):
        self.out: deque = deque(maxlen=deque_cap)
        self.inc: deque = deque(maxlen=deque_cap)
        self.credits: deque = deque(maxlen=deque_cap)
        self.cp: OrderedDict = (
            OrderedDict()
        )  # counterparty -> [last_out_ts, last_in_ts, out_cnt, in_cnt, out_amt]
        self.amt_hist: deque = deque(maxlen=amount_history_n)
        self.dwell_ewma: float = dwell_default

    def approx_bytes(self) -> int:
        return 400 + 120 * (len(self.out) + len(self.inc)) + 140 * len(self.credits) + 150 * len(self.cp)


def scan_out_24h(dq: deque, ts: float):
    """Sender debits: counts 10m/1h/24h, sums 1h/24h, new-payee and round counts 24h, distinct dsts 1h.

    The distinct dict keeps receivers in recency order (most recent first).
    """
    t10, t1, t24 = ts - 600.0, ts - 3600.0, ts - 86400.0
    c10 = c1 = c24 = 0
    a1 = a24 = 0.0
    newp = rnd = 0
    uniq: dict = {}
    for e in reversed(dq):
        te = e[0]
        if te <= t24:
            break
        c24 += 1
        a24 += e[2]
        newp += e[3]
        rnd += e[4]
        if te > t1:
            c1 += 1
            a1 += e[2]
            if e[1] not in uniq:
                uniq[e[1]] = None
            if te > t10:
                c10 += 1
    return c10, c1, c24, a1, a24, newp, rnd, uniq


def scan_in_24h(dq: deque, ts: float):
    """Sender credits: counts 1h/24h, sums 1h/24h, distinct payers 1h (recency order)."""
    t1, t24 = ts - 3600.0, ts - 86400.0
    c1 = c24 = 0
    a1 = a24 = 0.0
    uniq: dict = {}
    for e in reversed(dq):
        te = e[0]
        if te <= t24:
            break
        c24 += 1
        a24 += e[2]
        if te > t1:
            c1 += 1
            a1 += e[2]
            if e[1] not in uniq:
                uniq[e[1]] = None
    return c1, c24, a1, a24, uniq


def scan_in_1h(dq: deque, ts: float):
    """Receiver credits: counts 10m/1h, sum 1h, distinct payers 1h (recency order), first-contact
    payers 1h."""
    t10, t1 = ts - 600.0, ts - 3600.0
    c10 = c1 = 0
    a1 = 0.0
    uniq: dict = {}
    new: set = set()
    for e in reversed(dq):
        te = e[0]
        if te <= t1:
            break
        c1 += 1
        a1 += e[2]
        if e[1] not in uniq:
            uniq[e[1]] = None
        if e[3]:
            new.add(e[1])
        if te > t10:
            c10 += 1
    return c10, c1, a1, uniq, new


def scan_out_1h(dq: deque, ts: float):
    """Receiver debits: count 1h, sum 1h, distinct receivers 1h."""
    t1 = ts - 3600.0
    c1 = 0
    a1 = 0.0
    uniq: dict = {}
    for e in reversed(dq):
        if e[0] <= t1:
            break
        c1 += 1
        a1 += e[2]
        if e[1] not in uniq:
            uniq[e[1]] = None
    return c1, a1, uniq
