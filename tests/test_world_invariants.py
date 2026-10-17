"""Spec 17.2 (test_world_invariants): invariants §8.2.7 and per-typology checks on the tiny world."""

from __future__ import annotations

import bisect
import json
import re
from collections import defaultdict

from chainwatch.data.canonical import validate
from chainwatch.data.io import iter_events, read_accounts
from chainwatch.data.world.generate import generate_world
from tests.tiny import tiny_cfg

TINY_SEED = 7


def _auc(scores, labels):
    pairs = sorted(zip(scores, labels, strict=False))
    n1 = sum(1 for _, lab in pairs if lab == 1)
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return 0.5
    vals = sorted({s for s, _ in pairs})

    def avg_rank(v):
        lo = bisect.bisect_left(vals, v)
        hi = bisect.bisect_right(vals, v)
        return (lo + hi + 1) / 2.0

    u = sum(avg_rank(s) for s, lab in pairs if lab == 1) - n1 * (n1 + 1) / 2.0
    return u / (n1 * n0) if (n1 * n0) > 0 else 0.5


def test_tiny_world_invariants(tmp_workdir) -> None:
    cfg = tiny_cfg()
    wd = tmp_workdir / "world_tiny_seed7"
    report = generate_world(cfg, TINY_SEED, wd, progress=False)

    assert report["provenance"] == "ChainWorld"
    assert report["seed"] == TINY_SEED
    share = report["fraud_share"]
    assert 0.0 < share < 0.5
    assert report["campaigns_planned"] == cfg.world.n_campaigns
    assert report["rings_written"] == 12
    assert report["rings_invalid"] == []

    events = list(iter_events(wd / "events.csv.gz"))
    assert validate(events) == []
    eids = [le.event.event_id for le in events]
    assert eids == list(range(len(eids)))

    arch_cnt = report["archetype_counts"]
    assert arch_cnt["customer"] == 1365
    assert arch_cnt["merchant"] == 60
    assert arch_cnt["trader"] == 10
    assert arch_cnt["agent"] == 12
    assert arch_cnt["salary_payer"] == 4
    assert arch_cnt["landlord"] == 30

    cbt = report["counts_by_type"]
    for t in ("TRANSFER", "PAYMENT", "CASH_OUT", "CASH_IN", "DEBIT"):
        assert t in cbt and cbt[t] > 0

    acc = read_accounts(wd / "accounts.csv.gz")
    assert len(acc) == 1659
    for a in acc:
        assert re.fullmatch(r"A\d{8}", a["account_id"])
    ids = {a["account_id"] for a in acc}
    assert len(ids) == len(acc)

    idnums = [int(a["account_id"][1:]) for a in acc]
    mule = [a["is_mule"] for a in acc]
    merch = [1 if a["archetype"] == "merchant" else 0 for a in acc]
    agent = [1 if a["archetype"] == "agent" else 0 for a in acc]
    auc_m = _auc(idnums, mule)
    auc_k = _auc(idnums, merch)
    auc_ag = _auc(idnums, agent)
    assert 0.35 <= auc_m <= 0.65
    assert 0.35 <= auc_k <= 0.65
    assert 0.35 <= auc_ag <= 0.65

    by_ring = defaultdict(list)
    for le in events:
        if le.is_fraud:
            by_ring[le.ring_id].append(le)
    rings = json.loads((wd / "rings.json").read_text())["rings"]
    for r in rings:
        evs = by_ring[r["ring_id"]]
        inflow = sum(e.event.amount for e in evs if e.hop == 0)
        outflow = sum(e.event.amount for e in evs if e.dst_role == "sink")
        assert abs(inflow - r["inflow"]) < 0.03
        assert abs(outflow - r["outflow"]) < 0.03
        assert outflow <= inflow + 0.01
        in_m = defaultdict(float)
        out_m = defaultdict(float)
        for e in evs:
            if e.src_role in ("mule", "collector"):
                out_m[e.event.src] += e.event.amount
            if e.dst_role in ("mule", "collector"):
                in_m[e.event.dst] += e.event.amount
        for a, o in out_m.items():
            assert o <= in_m[a] + 0.02
        assert r["n_accounts"] >= 3
        assert r["n_hops"] >= 2

    struct_cap = 49999.0
    for r in rings:
        evs = by_ring[r["ring_id"]]
        typ = r["typology"]
        if typ in ("funnel", "structuring", "slow_roll"):
            pat = {(e.hop, e.src_role, e.dst_role) for e in evs}
            assert pat == {(0, "victim", "mule"), (1, "mule", "collector"), (2, "collector", "sink")}
        if typ == "layered_chain":
            mm = sorted({e.hop for e in evs if e.src_role == "mule" and e.dst_role == "mule"})
            sinkh = {e.hop for e in evs if e.dst_role == "sink"}
            assert mm and len(mm) >= 2
            assert sinkh == {max(e.hop for e in evs)}
            assert mm == list(range(1, len(mm) + 1))
            assert r["n_hops"] >= 3
        if typ == "split_merge":
            pat = {(e.hop, e.src_role, e.dst_role) for e in evs}
            assert pat == {
                (0, "victim", "mule"),
                (1, "mule", "mule"),
                (2, "mule", "collector"),
                (3, "collector", "sink"),
            }
            h2src = {e.event.src for e in evs if e.hop == 2}
            h2dst = {e.event.dst for e in evs if e.hop == 2}
            h1dst = {e.event.dst for e in evs if e.hop == 1}
            assert len(h2src) >= 2
            assert len(h2dst) == 1
            assert len(h1dst) >= 2
        if typ == "round_trip":
            mm = sorted({e.hop for e in evs if e.src_role == "mule" and e.dst_role == "mule"})
            assert mm and len(mm) >= 3
            h0dst = {e.event.dst for e in evs if e.hop == 0}
            ret = h0dst & {e.event.dst for e in evs if e.hop >= 2 and e.dst_role == "mule"}
            assert ret
        if typ == "structuring":
            assert all(e.event.amount <= struct_cap + 1e-6 for e in evs if e.hop >= 1)
        if typ == "slow_roll":
            pairs = defaultdict(int)
            for e in evs:
                if e.hop == 1:
                    pairs[(e.event.src, e.event.dst)] += 1
            assert min(pairs.values()) >= 3
            credits = defaultdict(list)
            for e in evs:
                if e.hop == 0:
                    credits[e.event.dst].append(e.event.ts)
            dwells = []
            for e in evs:
                if e.hop == 1 and credits[e.event.src]:
                    dwells.append(e.event.ts - min(credits[e.event.src]))
            assert dwells and min(dwells) >= 1800.0

    reuse_ring = next(r for r in rings if r["reused_mules"])
    assert len(reuse_ring["reused_mules"]) >= 1
    acc_by_id = {a["account_id"]: a for a in acc}
    for m in reuse_ring["reused_mules"]:
        assert m in acc_by_id
        assert acc_by_id[m]["is_mule"] == 1
