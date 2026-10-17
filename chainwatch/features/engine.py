"""Streaming feature engine (spec 9): the single feature implementation for training and serving (rule E4).

``FeatureEngine.step(event)`` computes every feature from strictly earlier events plus the fields of the
event itself (read phase), then updates all account state (apply phase). Pure Python: no numpy here, the
serving image has none.
"""

from __future__ import annotations

import logging
import math
from collections import OrderedDict, deque
from statistics import median

from chainwatch.features.registry import ALL_NAMES, names_for_cfg
from chainwatch.features.state import AccountState, scan_in_1h, scan_in_24h, scan_out_1h, scan_out_24h
from chainwatch.schema import Event

log = logging.getLogger(__name__)

FLOW_EDGES_MAXLEN = 500_000
_INF = float("inf")


class Plan:
    """Read-only FIFO plan for one debit (spec 9.6)."""

    __slots__ = ("share", "dwell", "n", "uniq", "depth", "origins", "takes")

    def __init__(self, share, dwell, n, uniq, depth, origins, takes):
        self.share = share
        self.dwell = dwell
        self.n = n
        self.uniq = uniq
        self.depth = depth
        self.origins = origins
        self.takes = takes


def plan_fifo(
    credits,
    amount: float,
    ts: float,
    lookback: float,
    trail_min_share: float,
    flow_min: float,
    depth_cap: int,
    funnel_cap: int,
    dwell_default: float,
) -> Plan:
    """Match a debit against the oldest unspent credits inside the lookback. Does not mutate state."""
    remaining = amount
    takes = []
    for c in credits:  # oldest first
        if ts - c[0] > lookback:  # expired: skipped here, popped in commit
            continue
        if remaining <= 1e-9:
            break
        t = c[1] if c[1] < remaining else remaining
        if t > 1e-9:
            takes.append((c, t))
            remaining -= t
    matched = amount - remaining
    share = matched / amount
    if matched > 0:
        wsum = 0.0
        srcs = set()
        for c, t in takes:
            wsum += t * (ts - c[0])
            srcs.add(c[2])
        dwell = wsum / matched
        n = len(takes)
        uniq = len(srcs)
        if share >= flow_min:
            depth_max = 0
            origins_sum = 0
            for c, t in takes:
                if t / matched >= trail_min_share:
                    if c[3] > depth_max:
                        depth_max = c[3]
                    origins_sum += c[4]
            depth = min(depth_cap, 1 + depth_max)
            origins = min(funnel_cap, origins_sum or 1)
        else:
            depth, origins = 0, 1
    else:
        dwell, n, uniq, depth, origins = dwell_default, 0, 0, 0, 1
    return Plan(share, dwell, n, uniq, depth, origins, takes)


def commit_fifo(credits: deque, plan: Plan, ts: float, lookback: float) -> None:
    for c, t in plan.takes:
        c[1] -= t
    while credits and (credits[0][1] <= 0.01 or ts - credits[0][0] > lookback):
        credits.popleft()


class FeatureEngine:
    """Stateful, single-threaded, strictly causal feature engine."""

    def __init__(self, cfg, detector_names: tuple[str, ...] | list[str] = ("G2",)):
        fc = cfg.features
        self.cfg = cfg
        self.tz = float(cfg.project.tz_offset_s)
        self.lookback = float(fc.fifo_lookback_s)
        self.state_ttl = float(fc.state_ttl_s)
        self.hist_n = int(fc.amount_history_n)
        self.alpha = float(fc.dwell_ewma_alpha)
        self.dwell_default = float(fc.dwell_default_s)
        self.depth_cap = int(fc.trail_depth_cap)
        self.trail_min_share = float(fc.trail_min_share)
        self.funnel_cap = int(fc.funnel_cap)
        self.susp_share = float(fc.susp_matched_share_min)
        self.susp_dwell = float(fc.susp_dwell_max_s)
        self.susp_in_uniq = int(fc.susp_min_in_uniq_1h)
        self.susp_ttl = float(fc.susp_ttl_s)
        self.flow_min = float(fc.flow_edge_min_matched)
        self.flow_every = float(fc.flow_rebuild_every_s)
        self.flow_cap = int(fc.flow_comp_cap)
        self.hub_cap = int(fc.hub_degree_cap)
        self.nbr_cap = int(fc.nbr_scan_cap)
        self.round_min = float(fc.round_min_amount)
        self.near_hi = float(fc.near_cap_inr)
        self.near_lo = float(fc.near_cap_lo_frac) * self.near_hi
        self.deque_cap = int(fc.deque_cap)
        self.payee_n = int(fc.payee_memory_n)
        self.flow_window = float(fc.flow_window_s)

        self.detectors = tuple(detector_names)
        names: list[str] = []
        for det in self.detectors:
            for n in names_for_cfg(cfg, det):
                if n not in names:
                    names.append(n)
        order = {n: i for i, n in enumerate(ALL_NAMES)}
        self.names = sorted(names, key=order.__getitem__)  # union of the detectors' columns
        active = set(self.names)
        self.use_graph = any(d.startswith("G2") for d in self.detectors)
        self.need_flow = self.use_graph and bool(active & {"g_flow_comp_size", "g_flow_comp_edges"})
        self.need_nbr_out = self.use_graph and "g_nbr_out_susp_cnt" in active
        self.need_dst_nbr = self.use_graph and "g_dst_nbr_in_susp_cnt" in active
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        self.clock = -_INF
        self.registry: dict[str, list] = {}  # id -> [first_seen, last_seen, susp_until, has_credit]
        self.heavy: OrderedDict[str, AccountState] = OrderedDict()
        self._hour_tick: int | None = None
        self._flow_tick: int | None = None
        self.flow_edges: deque = deque(maxlen=FLOW_EDGES_MAXLEN)
        self._flow_full_warned = False
        self._uf_parent: dict[str, str] = {}
        self._uf_size: dict[str, int] = {}
        self._uf_edges: dict[str, int] = {}
        self.n_events = 0
        self.n_evicted = 0

    def _reg(self, acct: str) -> list:
        r = self.registry.get(acct)
        if r is None:
            r = [None, None, 0.0, 0]
            self.registry[acct] = r
        return r

    def _state(self, acct: str) -> AccountState:
        st = self.heavy.get(acct)
        if st is None:
            st = AccountState(self.deque_cap, self.hist_n, self.dwell_default)
            self.heavy[acct] = st
        else:
            self.heavy.move_to_end(acct)
        return st

    def _sweep(self, ts: float) -> None:
        """Evict heavy state of accounts idle for ``state_ttl_s`` (LRU order equals last-seen order)."""
        limit = ts - self.state_ttl
        heavy, reg = self.heavy, self.registry
        while heavy:
            acct = next(iter(heavy))
            last = reg[acct][1]
            if last is None or last >= limit:
                break
            heavy.popitem(last=False)
            self.n_evicted += 1

    # ------------------------------------------------------------------ flow components (union-find)
    def _find(self, x: str) -> str:
        parent = self._uf_parent
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def _uf_add(self, a: str, b: str) -> None:
        parent, size, edges = self._uf_parent, self._uf_size, self._uf_edges
        for x in (a, b):
            if x not in parent:
                parent[x] = x
                size[x] = 1
                edges[x] = 0
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            edges[ra] += 1
            return
        if size[ra] < size[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        size[ra] += size[rb]
        edges[ra] += edges[rb] + 1
        del size[rb], edges[rb]

    def _rebuild_flow(self, tick_time: float) -> None:
        cutoff = tick_time - self.flow_window
        fe = self.flow_edges
        while fe and fe[0][0] <= cutoff:
            fe.popleft()
        self._uf_parent, self._uf_size, self._uf_edges = {}, {}, {}
        for _, a, b in fe:
            self._uf_add(a, b)

    def _comp(self, x: str) -> tuple[int, int]:
        if x not in self._uf_parent:
            return 1, 0
        r = self._find(x)
        return self._uf_size[r], self._uf_edges[r]

    # ------------------------------------------------------------------ main step
    def step(self, ev: Event) -> dict[str, float]:
        """Compute features for ``ev`` and update state. Returns a dict with all feature names."""
        if not isinstance(ev, Event):
            raise TypeError(f"FeatureEngine.step expects an Event, got {type(ev).__name__}")
        ts = ev.ts
        if ts < self.clock:
            ts = self.clock
        self.clock = ts
        ht = int(ts // 3600.0)
        if ht != self._hour_tick:
            self._hour_tick = ht
            self._sweep(ts)
        if self.need_flow:
            ft = int(ts // self.flow_every)
            if ft != self._flow_tick:
                self._flow_tick = ft
                self._rebuild_flow(ft * self.flow_every)

        src, dst, amount = ev.src, ev.dst, ev.amount
        rs, rd = self._reg(src), self._reg(dst)
        S, D = self._state(src), self._state(dst)

        # ================= READ PHASE (nothing below mutates state) =================
        typ = ev.type
        is_round = 1 if (amount >= self.round_min and round(amount, 2) % 100 == 0) else 0
        sbb = ev.src_bal_before
        sba = ev.src_bal_after
        if sbb is not None and sbb > 0:
            drain = amount / sbb
            drain = 0.0 if drain < 0 else (1.5 if drain > 1.5 else drain)
        else:
            drain = 0.0

        c10, c1, c24, oa1, oa24, newp24, rnd24, out_uniq = scan_out_24h(S.out, ts)
        ic1, ic24, ia1, ia24, in_uniq = scan_in_24h(S.inc, ts)
        s_first, s_last = rs[0], rs[1]
        cp_e = S.cp.get(dst)
        new_payee = 1 if (cp_e is None or cp_e[2] == 0) else 0
        s_out_uniq = len(out_uniq) + (0 if dst in out_uniq else 1)
        s_out_amt_1h = oa1 + amount
        s_in_uniq = len(in_uniq)
        if ia1 > 0:
            p1 = s_out_amt_1h / ia1
            pass1 = 0.0 if p1 < 0 else (2.0 if p1 > 2.0 else p1)
        else:
            pass1 = 0.0
        if ia24 > 0:
            p24 = (oa24 + amount) / ia24
            pass24 = 0.0 if p24 < 0 else (2.0 if p24 > 2.0 else p24)
        else:
            pass24 = 0.0
        hist = S.amt_hist
        if hist:
            r = amount / median(hist)
            amt_vs_med = 0.0 if r < 0 else (50.0 if r > 50.0 else r)
        else:
            amt_vs_med = 1.0

        plan = plan_fifo(
            S.credits,
            amount,
            ts,
            self.lookback,
            self.trail_min_share,
            self.flow_min,
            self.depth_cap,
            self.funnel_cap,
            self.dwell_default,
        )

        dc10, dc1, da1, d_in_uniq_d, d_new = scan_in_1h(D.inc, ts)
        doc1, doa1, d_out_uniq_d = scan_out_1h(D.out, ts)
        d_first, d_last = rd[0], rd[1]
        cpd = D.cp.get(src)
        first_contact = 1 if (cpd is None or cpd[3] == 0) else 0
        d_in_uniq = len(d_in_uniq_d) + (0 if src in d_in_uniq_d else 1)
        d_in_uniq_new = len(d_new) + (1 if (first_contact and src not in d_new) else 0)
        if da1 > 0:
            dp = doa1 / da1
            d_pass = 0.0 if dp < 0 else (2.0 if dp > 2.0 else dp)
        else:
            d_pass = 0.0

        if cp_e is not None and cp_e[2] > 0:
            e_pair = cp_e[2]
            r = amount / (cp_e[4] / cp_e[2])
            e_amt = 0.0 if r < 0 else (50.0 if r > 50.0 else r)
        else:
            e_pair = 0
            e_amt = 1.0
        e_rev = 1 if (cp_e is not None and cp_e[1] is not None and cp_e[1] > ts - 86400.0) else 0

        hub_s = (s_out_uniq + s_in_uniq) > self.hub_cap
        hub_d = (d_in_uniq + len(d_out_uniq_d)) > self.hub_cap

        g_src_susp = g_dst_susp = g_nbr_in = g_nbr_out = g_dst_nbr = 0
        comp_size, comp_edges = 1, 0
        if self.use_graph:
            reg = self.registry
            g_src_susp = 1 if rs[2] > ts else 0
            g_dst_susp = 1 if rd[2] > ts else 0
            k = 0
            for x in in_uniq:
                if k >= self.nbr_cap:
                    break
                k += 1
                if reg[x][2] > ts:
                    g_nbr_in += 1
            if self.need_nbr_out:
                k = 0
                for x in out_uniq:
                    if x == dst:
                        continue
                    if k >= self.nbr_cap:
                        break
                    k += 1
                    if reg[x][2] > ts:
                        g_nbr_out += 1
            if self.need_dst_nbr:
                k = 0
                for x in d_in_uniq_d:
                    if x == src:
                        continue
                    if k >= self.nbr_cap:
                        break
                    k += 1
                    if reg[x][2] > ts:
                        g_dst_nbr += 1
            if self.need_flow:
                ss, se = self._comp(src)
                ds, de = self._comp(dst)
                if ss >= ds:
                    comp_size, comp_edges = ss, se
                else:
                    comp_size, comp_edges = ds, de
                if comp_size > self.flow_cap:
                    comp_size = self.flow_cap
                if comp_edges > self.flow_cap:
                    comp_edges = self.flow_cap

        f = {
            "t_amount": amount,
            "t_log_amount": math.log1p(amount),
            "t_type_transfer": 1.0 if typ == "TRANSFER" else 0.0,
            "t_type_payment": 1.0 if typ == "PAYMENT" else 0.0,
            "t_type_cash_out": 1.0 if typ == "CASH_OUT" else 0.0,
            "t_type_cash_in": 1.0 if typ == "CASH_IN" else 0.0,
            "t_type_debit": 1.0 if typ == "DEBIT" else 0.0,
            "t_hour": float(int(((ts + self.tz) % 86400.0) // 3600.0)),
            "t_is_round": float(is_round),
            "t_near_cap": 1.0 if self.near_lo <= amount <= self.near_hi else 0.0,
            "t_s_drain": drain,
            "t_s_bal_after_zero": 1.0 if (sba is not None and sba < 1.0) else 0.0,
            "t_s_bal_known": 1.0 if sbb is not None else 0.0,
            "s_age_s": ts - s_first if s_first is not None else 0.0,
            "s_idle_s": ts - s_last if s_last is not None else 0.0,
            "s_out_cnt_10m": float(c10 + 1),
            "s_out_cnt_1h": float(c1 + 1),
            "s_out_cnt_24h": float(c24 + 1),
            "s_out_uniq_1h": float(s_out_uniq),
            "s_out_amt_1h": s_out_amt_1h,
            "s_in_cnt_1h": float(ic1),
            "s_in_cnt_24h": float(ic24),
            "s_in_uniq_1h": float(s_in_uniq),
            "s_in_amt_1h": ia1,
            "s_pass_ratio_1h": pass1,
            "s_pass_ratio_24h": pass24,
            "s_matched_share": plan.share,
            "s_dwell_s": plan.dwell,
            "s_dwell_ewma_s": S.dwell_ewma,
            "s_matched_n": float(plan.n),
            "s_matched_uniq_src": float(plan.uniq),
            "s_new_payee": float(new_payee),
            "s_new_payee_share_24h": (newp24 + new_payee) / (c24 + 1),
            "s_round_share_24h": (rnd24 + is_round) / (c24 + 1),
            "s_amt_vs_median": amt_vs_med,
            "d_age_s": ts - d_first if d_first is not None else 0.0,
            "d_idle_s": ts - d_last if d_last is not None else 0.0,
            "d_first_credit": 0.0 if rd[3] else 1.0,
            "d_in_cnt_10m": float(dc10 + 1),
            "d_in_cnt_1h": float(dc1 + 1),
            "d_in_uniq_1h": float(d_in_uniq),
            "d_in_uniq_new_1h": float(d_in_uniq_new),
            "d_in_amt_1h": da1 + amount,
            "d_out_cnt_1h": float(doc1),
            "d_out_uniq_1h": float(len(d_out_uniq_d)),
            "d_pass_ratio_1h": d_pass,
            "d_dwell_ewma_s": D.dwell_ewma,
            "e_pair_out_cnt": float(e_pair),
            "e_reverse_recent": float(e_rev),
            "e_amt_vs_pair_mean": e_amt,
            "g_trail_depth": float(plan.depth),
            "g_funnel_in": float(plan.origins),
            "g_src_susp": float(g_src_susp),
            "g_dst_susp": float(g_dst_susp),
            "g_nbr_in_susp_cnt": float(g_nbr_in),
            "g_nbr_out_susp_cnt": float(g_nbr_out),
            "g_dst_nbr_in_susp_cnt": float(g_dst_nbr),
            "g_flow_comp_size": float(comp_size),
            "g_flow_comp_edges": float(comp_edges),
        }

        # ================= APPLY PHASE (the single place where state changes) =================
        commit_fifo(S.credits, plan, ts, self.lookback)
        S.out.append((ts, dst, amount, new_payee, is_round))
        S.amt_hist.append(amount)
        D.inc.append((ts, src, amount, first_contact))
        D.credits.append([ts, amount, src, plan.depth, plan.origins])
        self._touch_cp(S, dst, True, ts, amount)
        self._touch_cp(D, src, False, ts, amount)
        if plan.share > 0:
            S.dwell_ewma = self.alpha * plan.dwell + (1.0 - self.alpha) * S.dwell_ewma
        if plan.share >= self.susp_share and plan.dwell <= self.susp_dwell and s_in_uniq >= self.susp_in_uniq:
            rs[2] = ts + self.susp_ttl
        if rs[0] is None:
            rs[0] = ts
        if rd[0] is None:
            rd[0] = ts
        rs[1] = ts
        rd[1] = ts
        rd[3] = 1
        if self.need_flow and plan.share >= self.flow_min and not hub_s and not hub_d:
            fe = self.flow_edges
            if len(fe) == FLOW_EDGES_MAXLEN and not self._flow_full_warned:
                log.warning("flow-edge deque is full (%d); oldest edges are dropped early", FLOW_EDGES_MAXLEN)
                self._flow_full_warned = True
            fe.append((ts, src, dst))
            self._uf_add(src, dst)
        self.n_events += 1
        return f

    def _touch_cp(self, st: AccountState, other: str, out: bool, ts: float, amount: float) -> None:
        cp = st.cp
        e = cp.get(other)
        if e is None:
            e = [None, None, 0, 0, 0.0]
            cp[other] = e
            if len(cp) > self.payee_n:
                cp.popitem(last=False)
        else:
            cp.move_to_end(other)
        if out:
            e[0] = ts
            e[2] += 1
            e[4] += amount
        else:
            e[1] = ts
            e[3] += 1

    # ------------------------------------------------------------------ helpers
    def vector(self, f: dict[str, float], names: list[str]) -> list[float]:
        return [f[n] for n in names]

    def stats(self, exact: bool = False) -> dict:
        import time as _time

        now = _time.monotonic()
        cache = getattr(self, "_approx_cache", None)
        if exact or cache is None or now - cache[0] > 10.0:
            approx = sum(st.approx_bytes() for st in self.heavy.values()) + 120 * len(self.registry)
            self._approx_cache = (now, approx)
        else:
            approx = cache[1]
        return {
            "events": self.n_events,
            "accounts": len(self.registry),
            "active_states": len(self.heavy),
            "evicted_states": self.n_evicted,
            "flow_edges": len(self.flow_edges),
            "approx_bytes": approx,
            "clock": self.clock if self.clock != -_INF else 0.0,
        }

    def account_view(self, acct: str) -> dict | None:
        """State summary for the UI side panel; ``None`` if the account was never seen."""
        r = self.registry.get(acct)
        if r is None:
            return None
        ts = self.clock
        st = self.heavy.get(acct)
        view = {
            "account_id": acct,
            "first_seen": r[0],
            "last_seen": r[1],
            "suspicious": bool(r[2] > ts),
            "susp_until": r[2] if r[2] > 0 else None,
            "state_active": st is not None,
        }
        if st is not None:
            c10, c1, c24, oa1, oa24, _, _, out_u = scan_out_24h(st.out, ts)
            ic1, ic24, ia1, ia24, in_u = scan_in_24h(st.inc, ts)
            view.update(
                {
                    "out_cnt_1h": c1,
                    "out_cnt_24h": c24,
                    "out_amt_1h": round(oa1, 2),
                    "out_amt_24h": round(oa24, 2),
                    "out_uniq_1h": len(out_u),
                    "in_cnt_1h": ic1,
                    "in_cnt_24h": ic24,
                    "in_amt_1h": round(ia1, 2),
                    "in_amt_24h": round(ia24, 2),
                    "in_uniq_1h": len(in_u),
                    "dwell_ewma_s": round(st.dwell_ewma, 1),
                    "fifo_head": [
                        {"ts": c[0], "remaining": round(c[1], 2), "src": c[2], "depth": c[3]}
                        for c in list(st.credits)[:5]
                    ],
                    "unspent_credits": len(st.credits),
                }
            )
        return view
