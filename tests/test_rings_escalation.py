"""Ring escalation (spec 11.5, 17.2): out to hold, in to verify, and what must not escalate.

Escalation only ever raises the tier of a *recommendation* (rule D2); the edge flag always uses the
base score, so ring membership cannot depend on whether escalation is on (spec 11.5).
"""

from __future__ import annotations

import pytest

from chainwatch.decision.tiers import Thresholds, decide
from chainwatch.rings.grouping import RingGrouper
from chainwatch.rings.offline import run_ring_layer
from chainwatch.schema import TYPE_CODE, Event

THR = Thresholds(verify=0.5, hold=0.8, edge=0.5, ring_alert=0.4)


@pytest.fixture(scope="module")
def rings_cfg(tiny_cfg_session):
    """The shipped [rings] section, copied so tests can toggle escalation knobs."""
    return dict(tiny_cfg_session.rings)


def _grouper(rings_cfg: dict, ring_alert: float = 0.4, edge: float = 0.5, **over) -> RingGrouper:
    cfg = dict(rings_cfg)
    cfg.update(over)
    return RingGrouper(cfg, edge, ring_alert)


def _ev(eid: int, ts: float, src: str, dst: str, typ: str = "TRANSFER", amount: float = 100.0) -> Event:
    return Event(eid, float(ts), src, dst, float(amount), typ)


def _alerting_ring(g: RingGrouper) -> str:
    """A two-edge chain: qualifies and crosses ring_alert = 0.4 immediately."""
    g.add_edge(_ev(0, 0, "A00000401", "A00000402"), 0.9)
    g.add_edge(_ev(1, 100, "A00000402", "A00000403"), 0.9)
    rings = g.rings()
    assert len(rings) == 1 and rings[0]["status"] == "alert"
    return rings[0]["ring_id"]


def test_escalates_money_leaving_a_member_out_to_hold(rings_cfg):
    g = _grouper(rings_cfg)
    ring_id = _alerting_ring(g)

    floor, esc_ring, side = g.floor_info("A00000402", "A00000990")
    assert (floor, esc_ring, side) == ("hold", ring_id, "out")
    assert g.member_floor("A00000402", "A00000990") == "hold"
    # entry members escalate too, not just relays
    assert g.member_floor("A00000401", "A00000990") == "hold"

    tier, eff = decide(0.1, THR, floor)  # weak base score, floored to hold
    assert tier == "hold"
    assert eff == pytest.approx(THR.hold)
    # a floor can only raise: a strong base score keeps its own effective score
    assert decide(0.9, THR, floor) == ("hold", 0.9)


def test_escalates_money_entering_a_member_in_to_verify(rings_cfg):
    g = _grouper(rings_cfg)
    ring_id = _alerting_ring(g)

    floor, esc_ring, side = g.floor_info("A00000990", "A00000402")
    assert (floor, esc_ring, side) == ("verify", ring_id, "in")
    assert g.member_floor("A00000990", "A00000402") == "verify"

    tier, eff = decide(0.1, THR, floor)
    assert tier == "verify"
    assert eff == pytest.approx(THR.verify)
    assert decide(0.9, THR, floor) == ("hold", 0.9)  # never lowers


def test_no_escalation_before_the_ring_alerts_or_for_non_members(rings_cfg):
    g = _grouper(rings_cfg, ring_alert=1.0)  # never reaches the alert threshold
    g.add_edge(_ev(0, 0, "A00000411", "A00000412"), 0.9)
    g.add_edge(_ev(1, 100, "A00000412", "A00000413"), 0.9)
    assert g.rings()[0]["status"] == "open"
    assert g.floor_info("A00000412", "A00000990") == (None, None, None)
    assert g.floor_info("A00000990", "A00000412") == (None, None, None)
    # a component that has not even qualified (single edge) escalates nothing
    g2 = _grouper(rings_cfg, ring_alert=0.4)
    g2.add_edge(_ev(0, 0, "A00000421", "A00000422"), 0.9)
    assert g2.rings() == []
    assert g2.floor_info("A00000422", "A00000990") == (None, None, None)


def test_escalation_uses_ring_state_before_the_current_event(rings_cfg):
    g = _grouper(rings_cfg, ring_alert=0.4)
    g.add_edge(_ev(0, 0, "A00000431", "A00000432"), 0.9)
    # the next event completes the ring; it must NOT be escalated by its own edge
    assert g.floor_info("A00000432", "A00000990") == (None, None, None)
    g.add_edge(_ev(1, 100, "A00000432", "A00000433"), 0.9)
    # from the following event on the ring is in alert state and floors apply
    assert g.floor_info("A00000432", "A00000990") == ("hold", "R0000001", "out")


def test_escalate_linking_only_keeps_terminal_exits_out(rings_cfg):
    rows = [
        (0, "A00000501", "A00000502", "TRANSFER"),
        (100, "A00000502", "A00000503", "TRANSFER"),
        (150, "A00000502", "A00000504", "CASH_OUT"),  # A00000504 is a non-linking exit member
    ]

    def build(**over):
        g = _grouper(rings_cfg, **over)
        for i, (ts, src, dst, typ) in enumerate(rows):
            g.add_edge(_ev(i, ts, src, dst, typ), 0.9)
        return g

    linking_only = build()  # shipped default: escalate_linking_only = true
    assert rings_cfg["escalation"] is True and rings_cfg["escalate_linking_only"] is True
    assert linking_only.floor_info("A00000998", "A00000504") == (None, None, None)  # money in
    assert linking_only.floor_info("A00000504", "A00000998") == (None, None, None)  # money out
    assert linking_only.floor_info("A00000502", "A00000998") == ("hold", "R0000001", "out")

    any_member = build(escalate_linking_only=False)  # control: every member escalates
    assert any_member.floor_info("A00000998", "A00000504") == ("verify", "R0000001", "in")
    assert any_member.floor_info("A00000504", "A00000998") == ("hold", "R0000001", "out")


def test_terminal_types_decide_who_is_a_linking_member(rings_cfg):
    # same stream as above but with only PAYMENT terminal: the cash-out receiver now links
    g = _grouper(rings_cfg, terminal_types=["PAYMENT"])
    rows = [
        (0, "A00000511", "A00000512", "TRANSFER"),
        (100, "A00000512", "A00000513", "TRANSFER"),
        (150, "A00000512", "A00000514", "CASH_OUT"),
    ]
    for i, (ts, src, dst, typ) in enumerate(rows):
        g.add_edge(_ev(i, ts, src, dst, typ), 0.9)
    assert g.floor_info("A00000997", "A00000514") == ("verify", "R0000001", "in")
    assert g.floor_info("A00000514", "A00000997") == ("hold", "R0000001", "out")


def test_escalation_switch_false_keeps_rings_but_removes_floors(rings_cfg):
    g = _grouper(rings_cfg, escalation=False)
    g.add_edge(_ev(0, 0, "A00000601", "A00000602"), 0.9)
    g.add_edge(_ev(1, 100, "A00000602", "A00000603"), 0.9)
    assert g.rings()[0]["status"] == "alert"  # ring formation is unaffected
    assert g.floor_info("A00000602", "A00000990") == (None, None, None)
    assert g.member_floor("A00000602", "A00000990") is None


def test_config_selects_the_escalation_tiers(rings_cfg):
    g = _grouper(rings_cfg, escalate_out_to="verify", escalate_in_to="hold")
    _alerting_ring(g)
    assert g.member_floor("A00000402", "A00000990") == "verify"
    assert g.member_floor("A00000990", "A00000402") == "hold"


# ------------------------------------------------------------------ offline ring layer

ROWS = [
    # ts, event_id, src, dst, amount, type, base score
    (0, 1, "A00000701", "A00000702", 1000.0, "TRANSFER", 0.9),  # flagged edge -> ring
    (100, 2, "A00000702", "A00000703", 900.0, "TRANSFER", 0.9),  # flagged edge -> ring alerts
    (200, 3, "A00000702", "A00000704", 800.0, "TRANSFER", 0.9),  # flagged edge -> membership grows
    (300, 4, "A00000702", "A00000990", 500.0, "TRANSFER", 0.1),  # member sends: below edge
    (400, 5, "A00000991", "A00000702", 500.0, "TRANSFER", 0.1),  # member receives: below edge
    (500, 6, "A00000990", "A00000991", 500.0, "PAYMENT", 0.1),  # unrelated accounts
]


def _stream():
    return {
        "ts": [r[0] for r in ROWS],
        "event_id": [r[1] for r in ROWS],
        "src": [r[2] for r in ROWS],
        "dst": [r[3] for r in ROWS],
        "amount": [r[4] for r in ROWS],
        "type": [TYPE_CODE[r[5]] for r in ROWS],
    }


def test_ring_layer_escalation_on_off_keeps_membership_identical(rings_cfg):
    scores = [r[6] for r in ROWS]
    on = run_ring_layer(_stream(), scores, THR, rings_cfg, escalation=True)
    off = run_ring_layer(_stream(), scores, THR, rings_cfg, escalation=False)

    # identical ring state: same rings, edges, members, peaks (edges use the base score)
    assert on.log == off.log
    assert on.stats == off.stats
    assert on.stats["edges_total"] == 3  # only the three base >= edge rows became edges
    assert [set(v["event_ids"]) for v in on.log.values()] == [{1, 2, 3}]

    # but the recommendations differ where the ring floored them
    assert on.tier == [2, 2, 2, 2, 1, 0]
    assert on.escalated == [0, 0, 0, 1, 1, 0]
    assert on.eff == pytest.approx([0.9, 0.9, 0.9, 0.8, 0.5, 0.1])
    assert off.tier == [2, 2, 2, 0, 0, 0]
    assert off.escalated == [0, 0, 0, 0, 0, 0]
    assert off.eff == pytest.approx([0.9, 0.9, 0.9, 0.1, 0.1, 0.1])


def test_ring_layer_escalation_never_feeds_ring_formation(rings_cfg):
    scores = [r[6] for r in ROWS]
    on = run_ring_layer(_stream(), scores, THR, rings_cfg, escalation=True)
    # rows 4 and 5 were escalated to hold/verify but stayed out of the grouper
    assert on.stats["edges_live"] == 3
    assert on.stats["alerts_total"] == 1
    for ring_log in on.log.values():
        assert ring_log["event_ids"] == {1, 2, 3}
        assert "A00000990" not in ring_log["accounts"]


def test_ring_layer_without_rings_ignores_escalation(rings_cfg):
    scores = [r[6] for r in ROWS]
    no_rings = run_ring_layer(_stream(), scores, THR, rings_cfg, use_rings=False, escalation=True)
    assert no_rings.tier == [2, 2, 2, 0, 0, 0]
    assert no_rings.log == {}
    assert no_rings.stats == {}
