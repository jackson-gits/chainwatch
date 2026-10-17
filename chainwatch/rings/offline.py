"""Offline ring layer: drive the serving ``RingGrouper`` and ``decide`` with precomputed scores
(spec 11.6, 14).

The order per event is the serving order (spec 4): ring tick, escalation floor from ring state *before*
the event, tier decision, then the edge (base score) goes to the grouper.
"""

from __future__ import annotations

from dataclasses import dataclass

from chainwatch.decision.tiers import Thresholds, decide
from chainwatch.rings.grouping import RingGrouper
from chainwatch.schema import EVENT_TYPES, Event

TIER_CODE = {"allow": 0, "verify": 1, "hold": 2}


@dataclass
class RingRun:
    tier: list[int]  # final tier per row (0 allow, 1 verify, 2 hold)
    eff: list[float]  # effective score per row (after escalation)
    escalated: list[int]  # 1 if escalation raised the tier
    log: dict  # ring_id -> {"peak", "alert_ts", "first_ts", "event_ids", "accounts", ...}
    stats: dict


def run_ring_layer(
    stream: dict,
    scores,
    thr: Thresholds,
    cfg_rings,
    use_rings: bool = True,
    escalation: bool | None = None,
    start: int = 0,
    stop: int | None = None,
) -> RingRun:
    """Run tiers (and the ring layer when ``use_rings``) over rows ``[start, stop)`` of a time-sorted stream.

    ``stream`` holds aligned sequences: ``ts``, ``event_id``, ``src``, ``dst``, ``amount``, ``type`` (codes).
    """
    stop = len(scores) if stop is None else stop
    ts_l = stream["ts"][start:stop]
    eid_l = stream["event_id"][start:stop]
    src_l = stream["src"][start:stop]
    dst_l = stream["dst"][start:stop]
    amt_l = stream["amount"][start:stop]
    typ_l = stream["type"][start:stop]
    sc_l = scores[start:stop]
    ts_l, eid_l, src_l, dst_l = _lst(ts_l), _lst(eid_l), _lst(src_l), _lst(dst_l)
    amt_l, typ_l, sc_l = _lst(amt_l), _lst(typ_l), _lst(sc_l)
    n = len(sc_l)
    tier = [0] * n
    eff = [0.0] * n
    esc = [0] * n
    if not use_rings:
        v, h = thr.verify, thr.hold
        for i, s in enumerate(sc_l):
            tier[i] = 2 if s >= h else (1 if s >= v else 0)
            eff[i] = s
        return RingRun(tier, eff, esc, {}, {})
    rc = dict(cfg_rings)
    if escalation is not None:
        rc["escalation"] = escalation
    g = RingGrouper(rc, thr.edge, thr.ring_alert, snapshots=False, keep_log=True)
    edge = thr.edge
    for i in range(n):
        ts = ts_l[i]
        g.tick(ts)
        src, dst, s = src_l[i], dst_l[i], sc_l[i]
        floor = g.member_floor(src, dst)
        t, e = decide(s, thr, floor)
        tc = TIER_CODE[t]
        tier[i] = tc
        eff[i] = e
        if floor is not None and tc > (2 if s >= thr.hold else (1 if s >= thr.verify else 0)):
            esc[i] = 1
        if s >= edge:
            g.add_edge(Event(int(eid_l[i]), ts, src, dst, float(amt_l[i]), EVENT_TYPES[int(typ_l[i])]), s)
    return RingRun(tier, eff, esc, g.log, g.stats())


def _lst(x):
    return x.tolist() if hasattr(x, "tolist") else list(x)
