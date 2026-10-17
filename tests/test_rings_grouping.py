"""Incremental ring grouping (spec 11.1, 11.2, 11.4, 11.8): what links, what does not.

All fixtures use opaque account ids only (rule D1).
"""

from __future__ import annotations

import random
import re

import pytest

from chainwatch.decision.reasons import DENY_WORDS
from chainwatch.rings.grouping import RingEvent, RingGrouper
from chainwatch.schema import Event


@pytest.fixture(scope="module")
def rings_cfg(tiny_cfg_session):
    """The shipped [rings] section of the tiny config (copied so tests can override it)."""
    return dict(tiny_cfg_session.rings)


def _grouper(rings_cfg: dict, ring_alert: float = 1.0, edge: float = 0.5, **over) -> RingGrouper:
    cfg = dict(rings_cfg)
    cfg.update(over)
    return RingGrouper(cfg, edge, ring_alert)


def _ev(eid: int, ts: float, src: str, dst: str, typ: str = "TRANSFER", amount: float = 100.0) -> Event:
    return Event(eid, float(ts), src, dst, float(amount), typ)


def _kinds(events) -> list[str]:
    return [e.kind for e in events]


def test_chain_of_three_edges_forms_a_ring(rings_cfg):
    g = _grouper(rings_cfg)
    out = []
    rows = [
        (0, "A00000001", "A00000002"),
        (100, "A00000002", "A00000003"),
        (200, "A00000003", "A00000004"),
    ]
    for i, (ts, src, dst) in enumerate(rows):
        out.extend(g.add_edge(_ev(i, ts, src, dst), 0.9))

    assert _kinds(out) == ["create", "update"]  # second edge qualifies, third changes membership
    rings = g.rings()
    assert len(rings) == 1
    snap = rings[0]
    assert re.fullmatch(r"R\d{7}", snap["ring_id"])
    assert snap["status"] == "open"  # ring_alert 1.0: no alert in this test
    assert (snap["n_accounts"], snap["n_edges"]) == (4, 3)
    assert snap["n_relays"] == 2  # both intermediate accounts relay
    assert snap["longest_chain"] == 3
    assert snap["ring_score"] == pytest.approx(0.999 * 0.75, abs=1e-6)
    roles = {a["id"]: a["role"] for a in snap["accounts"]}
    assert roles == {
        "A00000001": "entry",
        "A00000002": "relay",
        "A00000003": "relay",
        "A00000004": "exit",
    }
    assert len(snap["edges"]) == 3
    assert all(e["score"] == pytest.approx(0.9) for e in snap["edges"])


def test_two_edge_chain_qualifies_with_exactly_one_relay(rings_cfg):
    g = _grouper(rings_cfg)
    out = g.add_edge(_ev(0, 0, "A00000011", "A00000012"), 0.9)
    out += g.add_edge(_ev(1, 60, "A00000012", "A00000013"), 0.9)
    assert _kinds(out) == ["create"]
    snap = g.rings()[0]
    assert (snap["n_accounts"], snap["n_edges"], snap["n_relays"]) == (3, 2, 1)


def test_star_without_a_relay_does_not_form_a_ring(rings_cfg):
    # three independent senders into one receiver: connected, but nobody passes money on
    g = _grouper(rings_cfg)
    out = []
    for i, src in enumerate(["A00000021", "A00000022", "A00000023"]):
        out.extend(g.add_edge(_ev(i, i * 100, src, "A00000024"), 0.9))
    assert out == []  # stays a candidate: no create, update or alert event
    assert g.rings() == []
    st = g.stats()
    assert (st["open"], st["alert"], st["closed"]) == (0, 0, 0)
    assert st["edges_live"] == 3  # the edges are still grouped, just not visible


def test_terminal_receiver_is_a_member_but_never_links(rings_cfg):
    g = _grouper(rings_cfg)
    out = []
    rows = [
        (0, "A00000031", "A00000032", "TRANSFER"),
        (50, "A00000032", "A00000033", "TRANSFER"),
        (100, "A00000032", "A00000034", "CASH_OUT"),  # terminal: C joins as an exit member
        (150, "A00000034", "A00000035", "TRANSFER"),  # sends a new edge, still no link
    ]
    for i, (ts, src, dst, typ) in enumerate(rows):
        out.extend(g.add_edge(_ev(i, ts, src, dst, typ), 0.9))

    assert _kinds(out) == ["create", "update"]  # no merge after the exit sends money on
    assert len(g.rings()) == 1
    snap = g.rings()[0]
    assert (snap["n_accounts"], snap["n_edges"]) == (4, 3)  # A00000034->A00000035 stayed outside
    roles = {a["id"]: a["role"] for a in snap["accounts"]}
    assert roles["A00000034"] == "exit"
    assert snap["exit_amount"] == pytest.approx(100.0)  # only terminal edges count


def test_terminal_chain_does_not_link_but_transfer_chain_does(rings_cfg):
    # identical timestamps and structure; only the type differs
    def run(typ: str) -> RingGrouper:
        g = _grouper(rings_cfg)
        rows = [
            (0, "A00000041", "A00000042"),
            (100, "A00000042", "A00000043"),
            (200, "A00000043", "A00000044"),
        ]
        for i, (ts, src, dst) in enumerate(rows):
            g.add_edge(_ev(i, ts, src, dst, typ), 0.9)
        return g

    terminal = run("CASH_OUT")
    assert terminal.rings() == []
    assert terminal.stats()["edges_live"] == 3
    transfer = run("TRANSFER")
    assert len(transfer.rings()) == 1
    assert transfer.rings()[0]["n_accounts"] == 4


def test_hub_does_not_glue_two_rings_together(rings_cfg):
    def run(**over):
        g = _grouper(rings_cfg, **over)
        i = 0
        for k in range(31):  # push the hub past hub_degree_cap (30) with distinct counterparties
            g.add_edge(_ev(i, k * 10, f"A0000090{k:02d}", "A00000100"), 0.9)
            i += 1
        for ts, src, dst in [
            (4000, "A00000100", "A00000101"),  # hub is src: never links once it is a hub
            (4100, "A00000101", "A00000102"),
            (4200, "A00000100", "A00000103"),  # inside link_window_s of the previous edge
            (4300, "A00000103", "A00000104"),
        ]:
            g.add_edge(_ev(i, ts, src, dst), 0.9)
            i += 1
        return g

    hubbed = run()  # shipped hub_degree_cap = 30
    rings = hubbed.rings()
    assert len(rings) == 2, "the hub must not glue the two chains into one ring"
    sides = {frozenset(a["id"] for a in r["accounts"]) for r in rings}
    assert sides == {
        frozenset({"A00000100", "A00000101", "A00000102"}),
        frozenset({"A00000100", "A00000103", "A00000104"}),
    }
    assert all(r["n_edges"] == 2 for r in rings)

    glued = run(hub_degree_cap=100)  # control: without the cap the same edges merge
    assert len(glued.rings()) == 1
    assert glued.rings()[0]["n_accounts"] == 5
    assert glued.rings()[0]["n_edges"] == 4


def _two_rings(g: RingGrouper) -> list[str]:
    rows = [
        (0, "A00000201", "A00000202"),
        (50, "A00000202", "A00000203"),
        (10, "A00000211", "A00000212"),
        (60, "A00000212", "A00000213"),
    ]
    for i, (ts, src, dst) in enumerate(rows):
        g.add_edge(_ev(i, ts, src, dst), 0.9)
    return [r["ring_id"] for r in g.rings()]


def test_two_rings_merge_through_shared_relay_inside_link_window(rings_cfg):
    g = _grouper(rings_cfg)
    before = _two_rings(g)
    assert len(before) == 2
    out = g.add_edge(_ev(4, 150, "A00000202", "A00000212"), 0.9)  # bridge inside the window

    assert _kinds(out) == ["merge", "update"]
    assert out[0].ring_id == min(before)  # the smaller (older) id survives
    rings = g.rings()
    assert len(rings) == 1
    snap = rings[0]
    assert (snap["n_accounts"], snap["n_edges"]) == (6, 5)
    assert snap["n_relays"] == 2
    assert max(before) in snap["merged_from"]
    assert out[0].ring is not None and out[0].ring["ring_id"] == min(before)


def test_two_rings_do_not_merge_outside_link_window(rings_cfg):
    g = _grouper(rings_cfg)
    before = _two_rings(g)
    assert len(before) == 2
    out = g.add_edge(_ev(4, 4000, "A00000202", "A00000212"), 0.9)  # 3950s after the last links

    assert _kinds(out) == []  # the bridge forms its own invisible 2-account component
    rings = g.rings()
    assert sorted(r["ring_id"] for r in rings) == sorted(before)


def test_expiry_closes_a_ring_and_ids_stay_stable_across_rebuild(rings_cfg):
    g = _grouper(rings_cfg)
    g.add_edge(_ev(0, 0, "A00000301", "A00000302"), 0.9)
    g.add_edge(_ev(1, 100, "A00000302", "A00000303"), 0.9)
    ring1 = g.rings()[0]["ring_id"]
    g.add_edge(_ev(2, 20000, "A00000311", "A00000312"), 0.9)
    g.add_edge(_ev(3, 20100, "A00000312", "A00000313"), 0.9)
    ring2 = [r["ring_id"] for r in g.rings() if r["ring_id"] != ring1]
    assert len(ring2) == 1
    ring2 = ring2[0]

    out = g.tick(25000)  # cutoff 3400: only the first ring's edges expired
    assert _kinds(out) == ["close"]
    assert out[0].ring_id == ring1
    assert out[0].ring["status"] == "closed"

    live = g.rings()  # default status filter: open + alert
    assert [r["ring_id"] for r in live] == [ring2]
    assert live[0]["n_edges"] == 2  # identity and state survived the rebuild
    assert g.stats() == {
        "open": 1,
        "alert": 0,
        "closed": 1,
        "edges_live": 2,
        "edges_total": 4,
        "alerts_total": 0,
    }
    assert g.snapshot(ring1)["status"] == "closed"  # kept in bounded history
    assert g.snapshot(ring2)["status"] == "open"


def test_tick_is_a_noop_inside_one_boundary_and_after_expiry(rings_cfg):
    g = _grouper(rings_cfg)
    g.add_edge(_ev(0, 0, "A00000401", "A00000402"), 0.9)
    assert g.tick(1000) == []  # first boundary, nothing expired yet
    assert g.tick(1100) == []  # same rebuild_every_s bucket
    assert g.tick(1400) == []  # next bucket, still within ttl_s
    assert g.tick(1000) == []  # revisiting an old bucket is a no-op


def test_determinism_under_re_sorted_input(rings_cfg):
    # (eid, ts, src, dst, type, score), authored out of order; canonical order is (ts, eid)
    rows = [
        (7, 240, "A00000802", "A00000812", "TRANSFER", 0.9),
        (2, 60, "A00000812", "A00000813", "TRANSFER", 0.8),
        (6, 120, "A00000804", "A00000899", "TRANSFER", 0.6),
        (0, 0, "A00000801", "A00000802", "TRANSFER", 0.9),
        (5, 120, "A00000813", "A00000814", "TRANSFER", 0.8),
        (8, 300, "A00000851", "A00000852", "PAYMENT", 0.5),
        (1, 60, "A00000802", "A00000803", "TRANSFER", 0.9),
        (3, 0, "A00000811", "A00000812", "TRANSFER", 0.8),
        (4, 120, "A00000803", "A00000804", "CASH_OUT", 0.7),
    ]
    canonical = sorted(rows, key=lambda r: (r[1], r[0]))

    def run(seq) -> dict:
        g = _grouper(rings_cfg)
        events = []
        for eid, ts, src, dst, typ, score in seq:
            events.extend((e.kind, e.ts, e.ring_id) for e in g.add_edge(_ev(eid, ts, src, dst, typ), score))
        return {"events": events, "rings": g.rings(("open", "alert", "closed")), "stats": g.stats()}

    base = run(canonical)
    # the two chains qualify separately, then eid 7 merges them through the shared relays
    assert {"create", "merge"} <= {k for k, _ts, _rid in base["events"]}
    assert len(base["rings"]) == 1
    assert (base["rings"][0]["n_accounts"], base["rings"][0]["n_edges"]) == (8, 7)
    for seed in (1, 2, 3):
        shuffled = canonical[:]
        random.Random(seed).shuffle(shuffled)
        assert shuffled != canonical  # the shuffle really reordered the input
        resorted = sorted(shuffled, key=lambda r: (r[1], r[0]))
        assert resorted == canonical
        assert run(resorted) == base


def test_self_loops_are_ignored(rings_cfg):
    g = _grouper(rings_cfg)
    assert g.add_edge(_ev(0, 0, "A00000501", "A00000501"), 0.9) == []
    st = g.stats()
    assert st["edges_total"] == 0
    assert st["edges_live"] == 0


def test_ring_snapshot_shape_and_neutral_reasons(rings_cfg):
    g = _grouper(rings_cfg)
    g.add_edge(_ev(0, 0, "A00000061", "A00000062"), 0.9)
    g.add_edge(_ev(1, 60, "A00000062", "A00000063"), 0.9)
    snap = g.snapshot(g.rings()[0]["ring_id"])

    assert {
        "ring_id",
        "status",
        "first_ts",
        "last_ts",
        "alert_ts",
        "ring_score",
        "peak_score",
        "accounts",
        "edges",
        "n_accounts",
        "n_edges",
        "n_relays",
        "inflow",
        "outflow",
        "exit_amount",
        "longest_chain",
        "merged_from",
        "reasons",
    } <= set(snap)
    assert {"id", "role", "in_amount", "out_amount", "first_ts"} <= set(snap["accounts"][0])
    assert {"event_id", "ts", "src", "dst", "amount", "type", "score"} <= set(snap["edges"][0])

    assert [r["code"] for r in snap["reasons"]][0] == "RING_STRUCTURE"
    assert snap["reasons"][0]["text"].startswith("3 accounts are linked by 2 flagged transfers")
    for reason in snap["reasons"]:
        assert "{" not in reason["text"] and "}" not in reason["text"]
        lowered = reason["text"].lower()
        assert not any(w in lowered for w in DENY_WORDS)


def test_ring_event_to_dict(rings_cfg):
    g = _grouper(rings_cfg)
    assert g.add_edge(_ev(0, 5.0, "A00000071", "A00000072"), 0.9) == []  # 2 accounts: not visible
    g.add_edge(_ev(1, 60.0, "A00000072", "A00000073"), 0.9)
    g.add_edge(_ev(2, 120.0, "A00000073", "A00000074"), 0.9)
    events = g.add_edge(_ev(3, 180.0, "A00000074", "A00000075"), 0.9)
    assert events and isinstance(events[0], RingEvent)
    d = events[0].to_dict()
    assert set(d) == {"kind", "ts", "ring_id", "ring"}
    assert d["kind"] in {"create", "update", "merge", "alert", "close"}
    assert d["ring"]["ring_id"] == d["ring_id"]
