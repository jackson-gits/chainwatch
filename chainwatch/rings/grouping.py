"""Incremental ring grouping over flagged edges (spec 11.1, 11.2, 11.4). Pure Python.

A *flagged edge* is a scored transaction whose base score is at least the edge threshold. Two flagged
edges are in the same ring when they share a *linking* endpoint and their timestamps differ by at most
``link_window_s``. The sender always links; the receiver links unless the edge type is terminal
(cash-out, merchant payment, debit). Accounts with more than ``hub_degree_cap`` distinct flagged
counterparties inside ``ttl_s`` never link.

All operations are deterministic functions of the ordered flagged-edge sequence and the tick times.
"""

from __future__ import annotations

from collections import Counter, OrderedDict, deque

from chainwatch.rings.scoring import ring_score

TIER_ORDER = {"allow": 0, "verify": 1, "hold": 2}


class _Edge:
    __slots__ = ("eid", "event_id", "ts", "src", "dst", "amount", "typ", "score", "terminal")

    def __init__(self, eid, event_id, ts, src, dst, amount, typ, score, terminal):
        self.eid = eid
        self.event_id = event_id
        self.ts = ts
        self.src = src
        self.dst = dst
        self.amount = amount
        self.typ = typ
        self.score = score
        self.terminal = terminal


class _Member:
    __slots__ = (
        "in_amount",
        "out_amount",
        "n_in",
        "n_out",
        "first_ts",
        "first_in_ts",
        "first_out_ts",
        "last_in_ts",
        "relay",
        "linking",
    )

    def __init__(self, ts):
        self.in_amount = 0.0
        self.out_amount = 0.0
        self.n_in = 0
        self.n_out = 0
        self.first_ts = ts
        self.first_in_ts = None
        self.first_out_ts = None
        self.last_in_ts = None
        self.relay = False
        self.linking = False

    def absorb(self, o: _Member) -> None:
        self.in_amount += o.in_amount
        self.out_amount += o.out_amount
        self.n_in += o.n_in
        self.n_out += o.n_out
        self.first_ts = min(self.first_ts, o.first_ts)
        self.first_in_ts = _min_opt(self.first_in_ts, o.first_in_ts)
        self.first_out_ts = _min_opt(self.first_out_ts, o.first_out_ts)
        self.last_in_ts = _max_opt(self.last_in_ts, o.last_in_ts)
        self.relay = self.relay or o.relay
        self.linking = self.linking or o.linking


def _min_opt(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a if a <= b else b


def _max_opt(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


class _Comp:
    __slots__ = (
        "ring_id",
        "edges",
        "members",
        "first_ts",
        "last_ts",
        "score",
        "peak",
        "visible",
        "alerted",
        "alert_ts",
        "merged_from",
        "emitted_score",
        "emitted_members",
        "n_relays",
    )

    def __init__(self, ring_id, ts):
        self.ring_id = ring_id
        self.edges: list[int] = []
        self.members: dict[str, _Member] = {}
        self.first_ts = ts
        self.last_ts = ts
        self.score = 0.0
        self.peak = 0.0
        self.visible = False
        self.alerted = False
        self.alert_ts = None
        self.merged_from: list[str] = []
        self.emitted_score = 0.0
        self.emitted_members = 0
        self.n_relays = 0


class RingEvent:
    """``kind`` is create, update, merge, alert or close. ``ring`` is a snapshot dict (or None offline)."""

    __slots__ = ("kind", "ts", "ring_id", "ring")

    def __init__(self, kind, ts, ring_id, ring=None):
        self.kind = kind
        self.ts = ts
        self.ring_id = ring_id
        self.ring = ring

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ts": self.ts, "ring_id": self.ring_id, "ring": self.ring}


class RingGrouper:
    """Groups flagged edges into rings, tracks their lifecycle and answers escalation queries."""

    def __init__(
        self,
        cfg_rings,
        edge_threshold: float,
        ring_alert: float,
        snapshots: bool = True,
        keep_log: bool = False,
    ):
        r = cfg_rings
        self.min_accounts = int(r["min_accounts"])
        self.min_edges = int(r["min_flagged_edges"])
        self.link_window = float(r["link_window_s"])
        self.ttl = float(r["ttl_s"])
        self.rebuild_every = float(r["rebuild_every_s"])
        self.top_k = int(r["top_k_noisy_or"])
        self.require_pass = bool(r["require_pass_node"])
        self.hub_cap = int(r["hub_degree_cap"])
        self.terminal_types = frozenset(r["terminal_types"])
        self.escalation = bool(r.get("escalation", True))
        self.esc_out = str(r.get("escalate_out_to", "hold"))
        self.esc_in = str(r.get("escalate_in_to", "verify"))
        self.linking_only = bool(r.get("escalate_linking_only", True))
        self.max_edges_alert = int(r.get("max_edges_in_alert", 200))
        self.update_delta = float(r.get("update_delta", 0.1))
        self.edge_threshold = float(edge_threshold)
        self.ring_alert = float(ring_alert)
        self.snapshots = snapshots
        self.keep_log = keep_log
        self._next_ring = 1
        self._next_eid = 0
        self.history: deque = deque(maxlen=200)
        self.log: dict[str, dict] = {}
        self._reset_structure()
        self._tick: int | None = None
        self.n_edges_total = 0
        self.n_alerts = 0

    # ------------------------------------------------------------------ structure
    def _reset_structure(self) -> None:
        self.edges: dict[int, _Edge] = {}
        self.edge_q: deque = deque()
        self.parent: dict[int, int] = {}
        self.comp: dict[int, _Comp] = {}
        self.last_link: dict[str, tuple[float, int]] = {}
        self.cps: dict[str, OrderedDict] = {}
        self.acct_any: dict[str, int] = {}
        self.acct_link: dict[str, int] = {}

    def _new_ring_id(self) -> str:
        rid = f"R{self._next_ring:07d}"
        self._next_ring += 1
        return rid

    def _find(self, x: int) -> int:
        parent = self.parent
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def _comp_of(self, eid: int | None) -> _Comp | None:
        if eid is None or eid not in self.parent:
            return None
        return self.comp.get(self._find(eid))

    def _note_cp(self, acct: str, other: str, ts: float) -> None:
        od = self.cps.get(acct)
        if od is None:
            od = OrderedDict()
            self.cps[acct] = od
        od[other] = ts
        od.move_to_end(other)
        limit = ts - self.ttl
        while od and next(iter(od.values())) < limit:
            od.popitem(last=False)

    def _is_hub(self, acct: str) -> bool:
        od = self.cps.get(acct)
        return od is not None and len(od) > self.hub_cap

    def _union(self, a: int, b: int) -> tuple[int, _Comp | None]:
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            return ra, None
        A, B = self.comp[ra], self.comp[rb]
        if len(A.edges) < len(B.edges):
            ra, rb, A, B = rb, ra, B, A
        self.parent[rb] = ra
        del self.comp[rb]
        A.edges.extend(B.edges)
        for acct, m in B.members.items():
            mine = A.members.get(acct)
            if mine is None:
                A.members[acct] = m
            else:
                mine.absorb(m)
        A.first_ts = min(A.first_ts, B.first_ts)
        A.last_ts = max(A.last_ts, B.last_ts)
        A.peak = max(A.peak, B.peak)
        if B.ring_id < A.ring_id:
            A.ring_id, B.ring_id = B.ring_id, A.ring_id
        if B.ring_id:  # ids are empty strings only while rebuilding
            A.merged_from = sorted(set(A.merged_from + B.merged_from + [B.ring_id]))
        A.alerted = A.alerted or B.alerted
        A.alert_ts = _min_opt(A.alert_ts, B.alert_ts)
        was_visible = B.visible
        A.visible = A.visible or B.visible
        A.emitted_score = max(A.emitted_score, B.emitted_score)
        if self.keep_log:
            la, lb = self.log.get(A.ring_id), self.log.get(B.ring_id)
            if la is not None and lb is not None:
                la["event_ids"].update(lb["event_ids"])
                la["accounts"].update(lb["accounts"])
                la["peak"] = max(la["peak"], lb["peak"])
                la["first_ts"] = min(la["first_ts"], lb["first_ts"])
                la["alert_ts"] = _min_opt(la["alert_ts"], lb["alert_ts"])
                lb["merged_into"] = A.ring_id
        return ra, (B if was_visible else None)

    def _add_member(self, c: _Comp, acct: str, ts: float, amount: float, out: bool, linking: bool) -> None:
        m = c.members.get(acct)
        if m is None:
            m = _Member(ts)
            c.members[acct] = m
        if out:
            m.out_amount += amount
            m.n_out += 1
            if m.first_out_ts is None:
                m.first_out_ts = ts
            if m.last_in_ts is not None and ts - m.last_in_ts <= self.link_window:
                m.relay = True
        else:
            m.in_amount += amount
            m.n_in += 1
            if m.first_in_ts is None:
                m.first_in_ts = ts
            m.last_in_ts = ts
        if linking:
            m.linking = True

    def _qualifies(self, c: _Comp) -> bool:
        return (
            len(c.members) >= self.min_accounts
            and len(c.edges) >= self.min_edges
            and (c.n_relays >= 1 or not self.require_pass)
        )

    def _rescore(self, c: _Comp) -> None:
        c.n_relays = sum(1 for m in c.members.values() if m.relay)
        c.score = ring_score([self.edges[e].score for e in c.edges], c.n_relays, self.top_k)
        if c.score > c.peak:
            c.peak = c.score

    def _lifecycle(self, c: _Comp, ts: float, out: list) -> None:
        if not c.visible:
            if not self._qualifies(c):
                return
            c.visible = True
            out.append(self._event("create", ts, c))
            c.emitted_score, c.emitted_members = c.score, len(c.members)
        if not c.alerted and c.score >= self.ring_alert and self._qualifies(c):
            c.alerted = True
            c.alert_ts = ts
            self.n_alerts += 1
            out.append(self._event("alert", ts, c))
            c.emitted_score, c.emitted_members = c.score, len(c.members)
        elif len(c.members) != c.emitted_members or c.score - c.emitted_score >= self.update_delta:
            out.append(self._event("update", ts, c))
            c.emitted_score, c.emitted_members = c.score, len(c.members)

    def _event(self, kind: str, ts: float, c: _Comp) -> RingEvent:
        return RingEvent(kind, ts, c.ring_id, self._snapshot(c) if self.snapshots else None)

    def _update_log(self, c: _Comp, e: _Edge) -> None:
        lg = self.log.get(c.ring_id)
        if lg is None:
            lg = {
                "ring_id": c.ring_id,
                "event_ids": set(),
                "accounts": set(),
                "peak": 0.0,
                "first_ts": c.first_ts,
                "alert_ts": None,
                "merged_into": None,
            }
            self.log[c.ring_id] = lg
        lg["event_ids"].add(e.event_id)
        lg["accounts"].update((e.src, e.dst))
        lg["peak"] = max(lg["peak"], c.score)
        lg["first_ts"] = min(lg["first_ts"], c.first_ts)
        lg["alert_ts"] = _min_opt(lg["alert_ts"], c.alert_ts)

    # ------------------------------------------------------------------ public API
    def add_edge(self, ev, score: float) -> list[RingEvent]:
        """Add one flagged edge (the caller checked ``base score >= edge threshold``)."""
        src, dst, ts = ev.src, ev.dst, ev.ts
        if src == dst:
            return []
        out: list[RingEvent] = []
        eid = self._next_eid
        self._next_eid += 1
        self.n_edges_total += 1
        terminal = ev.type in self.terminal_types
        e = _Edge(eid, ev.event_id, ts, src, dst, ev.amount, ev.type, score, terminal)
        self._insert(e, out)
        return out

    def _insert(self, e: _Edge, out: list | None) -> _Comp:
        eid, src, dst, ts = e.eid, e.src, e.dst, e.ts
        self.edges[eid] = e
        self.edge_q.append(eid)
        self.parent[eid] = eid
        c = _Comp(self._new_ring_id() if out is not None else "", ts)
        c.edges.append(eid)
        self.comp[eid] = c
        self._note_cp(src, dst, ts)
        self._note_cp(dst, src, ts)
        link_src = not self._is_hub(src)
        link_dst = (not e.terminal) and not self._is_hub(dst)
        root = eid
        absorbed: list[_Comp] = []
        for x, linking in ((src, link_src), (dst, link_dst)):
            if not linking:
                continue
            ll = self.last_link.get(x)
            if ll is not None and ts - ll[0] <= self.link_window and ll[1] in self.edges:
                root, gone = self._union(root, ll[1])
                if gone is not None:
                    absorbed.append(gone)
            self.last_link[x] = (ts, eid)
        c = self.comp[self._find(root)]
        c.last_ts = max(c.last_ts, ts)
        self._add_member(c, src, ts, e.amount, True, link_src)
        self._add_member(c, dst, ts, e.amount, False, link_dst)
        self.acct_any[src] = eid
        self.acct_any[dst] = eid
        if link_src:
            self.acct_link[src] = eid
        if link_dst:
            self.acct_link[dst] = eid
        self._rescore(c)
        if out is not None:
            if self.keep_log:
                self._update_log(c, e)
            if absorbed:
                out.append(self._event("merge", ts, c))
            self._lifecycle(c, ts, out)
            if self.keep_log:
                self.log[c.ring_id]["alert_ts"] = _min_opt(self.log[c.ring_id]["alert_ts"], c.alert_ts)
                self.log[c.ring_id]["peak"] = max(self.log[c.ring_id]["peak"], c.score)
        return c

    def tick(self, ts: float) -> list[RingEvent]:
        """At each ``rebuild_every_s`` boundary drop expired edges and rebuild components if any expired."""
        tid = int(ts // self.rebuild_every)
        if tid == self._tick:
            return []
        self._tick = tid
        cutoff = ts - self.ttl
        expired = False
        while self.edge_q and self.edges[self.edge_q[0]].ts < cutoff:
            self.edge_q.popleft()
            expired = True
        if not expired:
            return []
        return self._rebuild(ts)

    def _rebuild(self, ts: float) -> list[RingEvent]:
        keep = list(self.edge_q)
        old_ring_of = {}
        old_comps: dict[str, _Comp] = {}
        for eid in self.edges:
            c = self._comp_of(eid)
            if c is not None:
                old_ring_of[eid] = c.ring_id
                old_comps[c.ring_id] = c
        old_edges = self.edges
        self._reset_structure()
        for eid in keep:
            self._insert(old_edges[eid], None)
        out: list[RingEvent] = []
        roots = sorted(self.comp, key=lambda r: min(self.comp[r].edges))
        taken: set[str] = set()
        for r in roots:
            c = self.comp[r]
            overlap = Counter(old_ring_of[e] for e in c.edges if e in old_ring_of)
            choice = None
            for rid, _n in sorted(overlap.items(), key=lambda kv: (-kv[1], kv[0])):
                if rid not in taken:
                    choice = rid
                    break
            if choice is None:
                c.ring_id = self._new_ring_id()
                # a fragment of rings that were already visible/alerting inherits that state
                # (no duplicate alerts)
                parents = [old_comps[rid] for rid in overlap]
                if parents:
                    c.visible = any(p.visible for p in parents)
                    c.alerted = any(p.alerted for p in parents)
                    alert_ts = [p.alert_ts for p in parents if p.alert_ts is not None]
                    c.alert_ts = min(alert_ts) if alert_ts else None
                    c.merged_from = sorted({p.ring_id for p in parents})
                    c.emitted_score, c.emitted_members = c.score, len(c.members)
            else:
                c.ring_id = choice
                old = old_comps[choice]
                c.visible = old.visible
                c.alerted = old.alerted
                c.alert_ts = old.alert_ts
                c.merged_from = old.merged_from
                c.emitted_score = old.emitted_score
                c.emitted_members = old.emitted_members
                c.first_ts = min(c.first_ts, old.first_ts)
                c.peak = max(c.peak, old.peak)
            taken.add(c.ring_id)
            if not c.visible:
                self._lifecycle(c, ts, out)
        for rid, old in old_comps.items():
            if rid not in taken and old.visible:
                snap = self._snapshot(old, status="closed", edges_src=old_edges)
                self.history.append(snap)
                out.append(RingEvent("close", ts, rid, snap if self.snapshots else None))
        return out

    def member_floor(self, src: str, dst: str) -> str | None:
        """Escalation floor (spec 11.5) from ring state *before* the current event."""
        return self.floor_info(src, dst)[0]

    def floor_info(self, src: str, dst: str) -> tuple[str | None, str | None, str | None]:
        """(floor, ring_id, side) where side is ``"out"`` (sender is a member) or ``"in"`` (receiver is)."""
        if not self.escalation:
            return None, None, None
        index = self.acct_link if self.linking_only else self.acct_any
        c = self._comp_of(index.get(src))
        if c is not None and c.alerted:
            return self.esc_out, c.ring_id, "out"
        c = self._comp_of(index.get(dst))
        if c is not None and c.alerted:
            return self.esc_in, c.ring_id, "in"
        return None, None, None

    def ring_of(self, acct: str) -> tuple[str, float, str] | None:
        """(ring_id, ring_score, status) of a visible ring the account belongs to, else None."""
        c = self._comp_of(self.acct_any.get(acct))
        if c is None or not c.visible:
            return None
        return c.ring_id, c.score, ("alert" if c.alerted else "open")

    def _find_ring(self, ring_id: str) -> _Comp | None:
        for c in self.comp.values():
            if c.ring_id == ring_id:
                return c
        return None

    def snapshot(self, ring_id: str) -> dict | None:
        c = self._find_ring(ring_id)
        if c is not None and c.visible:
            return self._snapshot(c)
        for snap in reversed(self.history):
            if snap["ring_id"] == ring_id:
                return snap
        return None

    def rings(self, status: tuple[str, ...] = ("open", "alert")) -> list[dict]:
        out = []
        for c in self.comp.values():
            if not c.visible:
                continue
            st = "alert" if c.alerted else "open"
            if st in status:
                out.append(self._snapshot(c))
        if "closed" in status:
            out.extend(self.history)
        out.sort(key=lambda s: (0 if s["status"] == "alert" else 1, -s["ring_score"], s["ring_id"]))
        return out

    def stats(self) -> dict:
        n_open = n_alert = 0
        for c in self.comp.values():
            if c.visible:
                if c.alerted:
                    n_alert += 1
                else:
                    n_open += 1
        return {
            "open": n_open,
            "alert": n_alert,
            "closed": len(self.history),
            "edges_live": len(self.edges),
            "edges_total": self.n_edges_total,
            "alerts_total": self.n_alerts,
        }

    # ------------------------------------------------------------------ snapshots
    def _snapshot(self, c: _Comp, status: str | None = None, edges_src: dict | None = None) -> dict:
        edges_src = edges_src if edges_src is not None else self.edges
        es = [edges_src[e] for e in c.edges if e in edges_src]
        roles = {}
        for acct, m in c.members.items():
            if m.relay:
                role = "relay"
            elif m.n_out and not m.n_in:
                role = "entry"
            elif m.n_in and not m.n_out:
                role = "exit"
            else:
                role = "entry" if (m.first_out_ts or 0) <= (m.first_in_ts or 0) else "exit"
            roles[acct] = role
        accounts = sorted(
            (
                {
                    "id": a,
                    "role": roles[a],
                    "in_amount": round(m.in_amount, 2),
                    "out_amount": round(m.out_amount, 2),
                    "first_ts": m.first_ts,
                }
                for a, m in c.members.items()
            ),
            key=lambda d: (d["first_ts"], d["id"]),
        )
        inflow = sum(e.amount for e in es if roles.get(e.src) == "entry")
        outflow = sum(e.amount for e in es if roles.get(e.dst) == "exit")
        exit_amount = sum(e.amount for e in es if e.terminal)
        best: dict[str, int] = {}
        longest = 0
        for e in sorted(es, key=lambda x: (x.ts, x.eid)):
            length = best.get(e.src, 0) + 1
            if length > best.get(e.dst, 0):
                best[e.dst] = length
            longest = max(longest, length)
        top = sorted(es, key=lambda x: (-x.amount, x.eid))[: self.max_edges_alert]
        relay_delays, relay_in, relay_out = [], 0.0, 0.0
        for m in c.members.values():
            if m.relay and m.first_in_ts is not None and m.first_out_ts is not None:
                relay_delays.append(max(0.0, m.first_out_ts - m.first_in_ts))
                relay_in += m.in_amount
                relay_out += m.out_amount
        snap = {
            "ring_id": c.ring_id,
            "status": status or ("alert" if c.alerted else ("open" if c.visible else "candidate")),
            "first_ts": c.first_ts,
            "last_ts": c.last_ts,
            "alert_ts": c.alert_ts,
            "ring_score": round(c.score, 6),
            "peak_score": round(c.peak, 6),
            "accounts": accounts,
            "edges": [
                {
                    "event_id": e.event_id,
                    "ts": e.ts,
                    "src": e.src,
                    "dst": e.dst,
                    "amount": e.amount,
                    "type": e.typ,
                    "score": round(e.score, 6),
                }
                for e in top
            ],
            "n_accounts": len(c.members),
            "n_edges": len(es),
            "n_relays": c.n_relays,
            "inflow": round(inflow, 2),
            "outflow": round(outflow, 2),
            "exit_amount": round(exit_amount, 2),
            "longest_chain": longest,
            "merged_from": list(c.merged_from),
        }
        from chainwatch.decision.reasons import ring_reasons

        snap["reasons"] = ring_reasons(snap, relay_delays, relay_in, relay_out)
        return snap
