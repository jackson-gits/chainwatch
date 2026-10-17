"""Independent, brute-force re-derivation of the P0 feature subset (test helper only).

Scope
-----
Only the 27 ``P0`` features of :mod:`chainwatch.features.registry` are implemented (groups T, S, D,
E and G; E contributes no P0 feature).  The module deliberately shares no code with
``chainwatch.features.engine`` or ``chainwatch.features.state``: every window, FIFO plan and graph
counter is recomputed here by scanning the stored history with explicit loops, so agreement between
the two is evidence rather than tautology.  It is *not* part of the shipped package and must stay
out of production imports.

Model
-----
* Events are fed in stream order.  A timestamp earlier than the running clock is clamped up to the
  clock exactly as the engine does (spec 9.1.8), and the clamped value is what gets stored and
  compared against windows.
* Per account we keep the same conceptual state: debit history, credit history, the FIFO of unspent
  credits, counterparty memory (one LRU shared by both directions, as in the engine) and the light
  registry (first/last seen and suspicious-until, which is never evicted).
* Heavy state (histories, FIFO, counterparty memory) expires after ``state_ttl_s`` of idleness,
  evaluated whenever the wall-clock hour changes.
* :meth:`ReferenceFeatures.step` returns exactly the 27 P0 names.
"""

from __future__ import annotations

import math
from collections import OrderedDict

from chainwatch.features.registry import FEATURES

#: the 27 P0 feature names in canonical registry order
P0_NAMES: tuple[str, ...] = tuple(f.name for f in FEATURES if f.priority == "P0")


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


class ReferenceFeatures:
    """Obviously-correct P0 computation for one (clamped) event stream."""

    def __init__(self, cfg) -> None:
        fc = cfg.features
        self.tz = float(cfg.project.tz_offset_s)
        self.lookback = float(fc.fifo_lookback_s)
        self.state_ttl = float(fc.state_ttl_s)
        self.deque_cap = int(fc.deque_cap)
        self.payee_n = int(fc.payee_memory_n)
        self.dwell_default = float(fc.dwell_default_s)
        self.depth_cap = int(fc.trail_depth_cap)
        self.trail_min_share = float(fc.trail_min_share)
        self.flow_min = float(fc.flow_edge_min_matched)
        self.round_min = float(fc.round_min_amount)
        self.near_hi = float(fc.near_cap_inr)
        self.near_lo = float(fc.near_cap_lo_frac) * self.near_hi
        self.susp_share = float(fc.susp_matched_share_min)
        self.susp_dwell = float(fc.susp_dwell_max_s)
        self.susp_in_uniq = int(fc.susp_min_in_uniq_1h)
        self.susp_ttl = float(fc.susp_ttl_s)
        self.nbr_cap = int(fc.nbr_scan_cap)
        self.reset()

    def reset(self) -> None:
        self.clock = -math.inf
        self._hour: int | None = None
        self.first: dict[str, float] = {}
        self.last: dict[str, float] = {}
        self.susp_until: dict[str, float] = {}
        self.out: dict[str, list] = {}
        self.inc: dict[str, list] = {}
        self.credits: dict[str, list] = {}
        self.cp: dict[str, OrderedDict] = {}
        self.n_events = 0

    # ------------------------------------------------------------------ state helpers
    def _out(self, acct: str) -> list:
        return self.out.setdefault(acct, [])

    def _inc(self, acct: str) -> list:
        return self.inc.setdefault(acct, [])

    def _credits(self, acct: str) -> list:
        return self.credits.setdefault(acct, [])

    def _cp(self, acct: str) -> OrderedDict:
        cpd = self.cp.get(acct)
        if cpd is None:
            cpd = self.cp[acct] = OrderedDict()
        return cpd

    def _append_capped(self, lst: list, item) -> None:
        lst.append(item)
        if len(lst) > self.deque_cap:
            del lst[0]

    def _sweep(self, ts: float) -> None:
        """Evict heavy state of accounts idle for at least ``state_ttl_s`` (registry survives)."""
        limit = ts - self.state_ttl
        stale = [a for a, last in self.last.items() if last is not None and last < limit]
        for acct in stale:
            self.out.pop(acct, None)
            self.inc.pop(acct, None)
            self.credits.pop(acct, None)
            self.cp.pop(acct, None)

    @staticmethod
    def _distinct_recency(entries: list, cutoff: float, idx: int) -> list:
        """Distinct values at ``idx`` with ``ts > cutoff``, most recent first (engine order)."""
        seen: set = set()
        out: list = []
        for e in reversed(entries):
            if e[0] <= cutoff:
                break
            if e[idx] not in seen:
                seen.add(e[idx])
                out.append(e[idx])
        return out

    # ------------------------------------------------------------------ FIFO plan (spec 9.6)
    def _plan_fifo(self, credits: list, amount: float, ts: float):
        remaining = amount
        takes = []
        for c in credits:  # oldest first
            if ts - c[0] > self.lookback:  # expired credits are skipped, never matched
                continue
            if remaining <= 1e-9:
                break
            take = c[1] if c[1] < remaining else remaining
            if take > 1e-9:
                takes.append((c, take))
                remaining -= take
        matched = amount - remaining
        share = matched / amount
        if matched > 0:
            dwell = sum(take * (ts - c[0]) for c, take in takes) / matched
            if share >= self.flow_min:
                depth_max = 0
                for c, take in takes:
                    if take / matched >= self.trail_min_share and c[3] > depth_max:
                        depth_max = c[3]
                depth = min(self.depth_cap, 1 + depth_max)
            else:
                depth = 0
        else:
            dwell, depth = self.dwell_default, 0
        return share, dwell, depth, takes

    def _touch_cp(self, acct: str, other: str, is_out: bool) -> None:
        cp = self._cp(acct)
        e = cp.get(other)
        if e is None:
            e = [0, 0]  # [out_cnt, in_cnt]
            cp[other] = e
            if len(cp) > self.payee_n:
                cp.popitem(last=False)
        else:
            cp.move_to_end(other)
        e[0 if is_out else 1] += 1

    # ------------------------------------------------------------------ main step
    def step(self, ev) -> dict[str, float]:
        ts = ev.ts
        if ts < self.clock:
            ts = self.clock
        self.clock = ts
        hour = int(ts // 3600.0)
        if hour != self._hour:
            self._hour = hour
            self._sweep(ts)

        src, dst, amount = ev.src, ev.dst, ev.amount
        if amount <= 0:
            raise ValueError("Event.amount must be positive")

        out_s, inc_s = self._out(src), self._inc(src)
        inc_d, out_d = self._inc(dst), self._out(dst)

        # prior sender history (1 h window, half-open: ts - W, ts])
        c1 = sum(1 for row in out_s if row[0] > ts - 3600.0)
        oa1 = sum(row[2] for row in out_s if row[0] > ts - 3600.0)
        in_uniq_s = self._distinct_recency(inc_s, ts - 3600.0, 1)
        ia1 = sum(row[2] for row in inc_s if row[0] > ts - 3600.0)
        if ia1 > 0:
            pass1 = _clip((oa1 + amount) / ia1, 0.0, 2.0)
        else:
            pass1 = 0.0

        # prior receiver history
        dc10 = sum(1 for row in inc_d if row[0] > ts - 600.0)
        da1 = sum(row[2] for row in inc_d if row[0] > ts - 3600.0)
        in_uniq_d = self._distinct_recency(inc_d, ts - 3600.0, 1)
        d_new = {row[1] for row in inc_d if row[0] > ts - 3600.0 and row[3]}
        doa1 = sum(row[2] for row in out_d if row[0] > ts - 3600.0)
        if da1 > 0:
            d_pass = _clip(doa1 / da1, 0.0, 2.0)
        else:
            d_pass = 0.0

        cp_s = self._cp(src).get(dst)
        new_payee = 1 if (cp_s is None or cp_s[0] == 0) else 0
        cp_d = self._cp(dst).get(src)
        first_contact = 1 if (cp_d is None or cp_d[1] == 0) else 0

        share, dwell, depth, takes = self._plan_fifo(self._credits(src), amount, ts)

        d_in_uniq = len(in_uniq_d) + (0 if src in in_uniq_d else 1)
        d_in_uniq_new = len(d_new) + (1 if (first_contact and src not in d_new) else 0)
        g_src_susp = 1 if self.susp_until.get(src, 0.0) > ts else 0
        g_dst_susp = 1 if self.susp_until.get(dst, 0.0) > ts else 0
        g_nbr_in = 0
        for payer in in_uniq_s[: self.nbr_cap]:
            if self.susp_until.get(payer, 0.0) > ts:
                g_nbr_in += 1

        is_round = 1 if (amount >= self.round_min and round(amount, 2) % 100 == 0) else 0
        sbb, sba = ev.src_bal_before, ev.src_bal_after
        drain = _clip(amount / sbb, 0.0, 1.5) if (sbb is not None and sbb > 0) else 0.0

        f = {
            "t_amount": amount,
            "t_log_amount": math.log1p(amount),
            "t_type_transfer": 1.0 if ev.type == "TRANSFER" else 0.0,
            "t_type_payment": 1.0 if ev.type == "PAYMENT" else 0.0,
            "t_type_cash_out": 1.0 if ev.type == "CASH_OUT" else 0.0,
            "t_type_cash_in": 1.0 if ev.type == "CASH_IN" else 0.0,
            "t_type_debit": 1.0 if ev.type == "DEBIT" else 0.0,
            "t_hour": float(int(((ts + self.tz) % 86400.0) // 3600.0)),
            "t_is_round": float(is_round),
            "t_near_cap": 1.0 if self.near_lo <= amount <= self.near_hi else 0.0,
            "t_s_drain": drain,
            "t_s_bal_after_zero": 1.0 if (sba is not None and sba < 1.0) else 0.0,
            "t_s_bal_known": 1.0 if sbb is not None else 0.0,
            "s_out_cnt_1h": float(c1 + 1),
            "s_in_uniq_1h": float(len(in_uniq_s)),
            "s_pass_ratio_1h": pass1,
            "s_matched_share": share,
            "s_dwell_s": dwell,
            "s_new_payee": float(new_payee),
            "d_in_cnt_10m": float(dc10 + 1),
            "d_in_uniq_1h": float(d_in_uniq),
            "d_in_uniq_new_1h": float(d_in_uniq_new),
            "d_pass_ratio_1h": d_pass,
            "g_trail_depth": float(depth),
            "g_src_susp": float(g_src_susp),
            "g_dst_susp": float(g_dst_susp),
            "g_nbr_in_susp_cnt": float(g_nbr_in),
        }

        # ------------------------------ apply phase (only after every feature was read)
        credits_s = self._credits(src)
        for c, take in takes:
            c[1] -= take
        while credits_s and (credits_s[0][1] <= 0.01 or ts - credits_s[0][0] > self.lookback):
            credits_s.pop(0)
        self._append_capped(out_s, (ts, dst, amount, new_payee, is_round))
        self._append_capped(inc_d, (ts, src, amount, first_contact))
        self._append_capped(self._credits(dst), [ts, amount, src, depth])
        self._touch_cp(src, dst, True)
        self._touch_cp(dst, src, False)
        if share >= self.susp_share and dwell <= self.susp_dwell and len(in_uniq_s) >= self.susp_in_uniq:
            self.susp_until[src] = ts + self.susp_ttl
        if src not in self.first:
            self.first[src] = ts
        if dst not in self.first:
            self.first[dst] = ts
        self.last[src] = ts
        self.last[dst] = ts
        self.n_events += 1
        return f
