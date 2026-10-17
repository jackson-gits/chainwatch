"""Edge-case streams for the streaming FeatureEngine (spec 17.2: test_features_edge_cases.py).

Every stream is hand-built from ``Event`` objects and fed through ``FeatureEngine(cfg, ("G2",))``.
These tests pin the *actual* behaviour of the engine (windows, FIFO, suspicion, flow, caps,
eviction), including the boundaries where a value is just inside or just outside a window.
"""

from __future__ import annotations

import pytest

from chainwatch.features.engine import FeatureEngine
from chainwatch.features.registry import ALL_NAMES, feature_names
from chainwatch.schema import Event

DWELL_DEFAULT = 86400.0  # features.dwell_default_s in configs/default.toml


def _ev(eid, ts, src, dst, amount, typ="TRANSFER", sbb=None, sba=None) -> Event:
    """Opaque synthetic accounts only (rule D1)."""
    return Event(eid, float(ts), src, dst, float(amount), typ, sbb, sba, None, None)


def _run(cfg, events) -> list[dict]:
    eng = FeatureEngine(cfg, ("G2",))
    return [eng.step(e) for e in events]


# ------------------------------------------------------------------ first event, T group
def test_first_event_of_a_brand_new_account(tiny_cfg_session):
    """Spec 9.3 defaults: every feature has a defined value on the very first event."""
    f = _run(
        tiny_cfg_session,
        [_ev(1, 5400, "A00000001", "A00000002", 49999.0, "CASH_OUT", 100000.0, 50001.0)],
    )[0]
    tz = tiny_cfg_session.project.tz_offset_s
    assert f == pytest.approx(
        {
            "t_amount": 49999.0,
            "t_log_amount": 10.819778284410283,  # math.log1p(49999)
            "t_type_transfer": 0.0,
            "t_type_payment": 0.0,
            "t_type_cash_out": 1.0,
            "t_type_cash_in": 0.0,
            "t_type_debit": 0.0,
            "t_hour": float((5400 + tz) // 3600 % 24),
            "t_is_round": 0.0,  # 49999 is not a multiple of 100
            "t_near_cap": 1.0,  # 0.85 * 49999 <= 49999 <= 49999
            "t_s_drain": 0.49999,
            "t_s_bal_after_zero": 0.0,
            "t_s_bal_known": 1.0,
            "s_age_s": 0.0,
            "s_idle_s": 0.0,
            "s_out_cnt_10m": 1.0,
            "s_out_cnt_1h": 1.0,
            "s_out_cnt_24h": 1.0,
            "s_out_uniq_1h": 1.0,
            "s_out_amt_1h": 49999.0,
            "s_in_cnt_1h": 0.0,
            "s_in_cnt_24h": 0.0,
            "s_in_uniq_1h": 0.0,
            "s_in_amt_1h": 0.0,
            "s_pass_ratio_1h": 0.0,
            "s_pass_ratio_24h": 0.0,
            "s_matched_share": 0.0,
            "s_dwell_s": DWELL_DEFAULT,
            "s_dwell_ewma_s": DWELL_DEFAULT,
            "s_matched_n": 0.0,
            "s_matched_uniq_src": 0.0,
            "s_new_payee": 1.0,
            "s_new_payee_share_24h": 1.0,
            "s_round_share_24h": 0.0,  # not a round amount
            "s_amt_vs_median": 1.0,
            "d_age_s": 0.0,
            "d_idle_s": 0.0,
            "d_first_credit": 1.0,
            "d_in_cnt_10m": 1.0,
            "d_in_cnt_1h": 1.0,
            "d_in_uniq_1h": 1.0,
            "d_in_uniq_new_1h": 1.0,
            "d_in_amt_1h": 49999.0,
            "d_out_cnt_1h": 0.0,
            "d_out_uniq_1h": 0.0,
            "d_pass_ratio_1h": 0.0,
            "d_dwell_ewma_s": DWELL_DEFAULT,
            "e_pair_out_cnt": 0.0,
            "e_reverse_recent": 0.0,
            "e_amt_vs_pair_mean": 1.0,
            "g_trail_depth": 0.0,
            "g_funnel_in": 1.0,
            "g_src_susp": 0.0,
            "g_dst_susp": 0.0,
            "g_nbr_in_susp_cnt": 0.0,
            "g_nbr_out_susp_cnt": 0.0,
            "g_dst_nbr_in_susp_cnt": 0.0,
            "g_flow_comp_size": 1.0,
            "g_flow_comp_edges": 0.0,
        },
        abs=1e-9,
    )
    assert set(f) == set(ALL_NAMES)  # every feature key present for a G2 engine


def test_t_is_round_and_near_cap_boundaries(tiny_cfg_session):
    lo = float(tiny_cfg_session.features.near_cap_lo_frac) * float(tiny_cfg_session.features.near_cap_inr)
    hi = float(tiny_cfg_session.features.near_cap_inr)
    amounts_round = [500.0, 1000.0, 1500.0, 1234.56, 100000.0]
    expected_round = [0.0, 1.0, 1.0, 0.0, 1.0]
    fs = _run(
        tiny_cfg_session,
        [_ev(i, 10.0, "A00000001", "A00000002", a) for i, a in enumerate(amounts_round)],
    )
    assert [f["t_is_round"] for f in fs] == expected_round

    amounts_cap = [hi, lo, lo - 0.01, hi + 0.01]
    expected_cap = [1.0, 1.0, 0.0, 0.0]
    fs = _run(
        tiny_cfg_session,
        [_ev(i, 10.0, "A00000001", "A00000002", a) for i, a in enumerate(amounts_cap)],
    )
    assert [f["t_near_cap"] for f in fs] == expected_cap


def test_t_s_drain_clipping_and_missing_balance(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 10.0, "S01", "D01", 500.0, sbb=100.0),  # 5.0 -> clipped to 1.5
            _ev(2, 20.0, "S01", "D01", 500.0, sbb=0.0),  # not above 0 -> 0
            _ev(3, 30.0, "S01", "D01", 500.0, sbb=None),  # unknown -> 0
            _ev(4, 40.0, "S01", "D01", 500.0, sbb=100000.0, sba=0.5),  # drain = 500/100000
        ],
    )
    assert [f["t_s_drain"] for f in fs] == [1.5, 0.0, 0.0, 0.005]
    assert [f["t_s_bal_known"] for f in fs] == [1.0, 1.0, 0.0, 1.0]
    assert [f["t_s_bal_after_zero"] for f in fs] == [0.0, 0.0, 0.0, 1.0]


# ------------------------------------------------------------------ window boundaries
def test_window_1h_out_boundary_is_half_open(tiny_cfg_session):
    # exactly ts - 3600 is OUTSIDE the window (ts - W, ts]
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "S01", "D01", 100), _ev(2, 4600, "S01", "D02", 200)])
    f = fs[1]
    assert f["s_out_cnt_1h"] == 1.0
    assert f["s_out_uniq_1h"] == 1.0
    assert f["s_out_amt_1h"] == 200.0
    assert f["s_age_s"] == 3600.0
    assert f["s_idle_s"] == 3600.0
    # one second earlier the old event IS inside
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "S01", "D01", 100), _ev(2, 4599, "S01", "D02", 200)])
    f = fs[1]
    assert f["s_out_cnt_1h"] == 2.0
    assert f["s_out_uniq_1h"] == 2.0
    assert f["s_out_amt_1h"] == 300.0
    assert f["s_age_s"] == 3599.0
    assert f["s_idle_s"] == 3599.0


def test_window_1h_in_boundary_prior_only(tiny_cfg_session):
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "P01", "D01", 100), _ev(2, 4600, "D01", "X01", 50)])
    f = fs[1]
    assert f["s_in_cnt_1h"] == 0.0
    assert f["s_in_uniq_1h"] == 0.0
    assert f["s_in_amt_1h"] == 0.0
    assert f["s_pass_ratio_1h"] == 0.0
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "P01", "D01", 100), _ev(2, 4599, "D01", "X01", 50)])
    f = fs[1]
    assert f["s_in_cnt_1h"] == 1.0
    assert f["s_in_uniq_1h"] == 1.0
    assert f["s_in_amt_1h"] == 100.0
    assert f["s_pass_ratio_1h"] == 0.5  # (0 + 50) / 100


def test_window_10m_receiver_boundary(tiny_cfg_session):
    fs = _run(tiny_cfg_session, [_ev(1, 0, "P01", "D01", 10), _ev(2, 600, "P02", "D01", 20)])
    assert fs[1]["d_in_cnt_10m"] == 1.0
    fs = _run(tiny_cfg_session, [_ev(1, 0, "P01", "D01", 10), _ev(2, 599, "P02", "D01", 20)])
    assert fs[1]["d_in_cnt_10m"] == 2.0
    # sender side of the same boundary
    fs = _run(tiny_cfg_session, [_ev(1, 0, "S01", "D01", 10), _ev(2, 600, "S01", "D02", 10)])
    assert fs[1]["s_out_cnt_10m"] == 1.0
    fs = _run(tiny_cfg_session, [_ev(1, 0, "S01", "D01", 10), _ev(2, 599, "S01", "D02", 10)])
    assert fs[1]["s_out_cnt_10m"] == 2.0


def test_window_24h_boundary(tiny_cfg_session):
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "S01", "D01", 100), _ev(2, 87400, "S01", "D02", 100)])
    assert fs[1]["s_out_cnt_24h"] == 1.0
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "S01", "D01", 100), _ev(2, 87399, "S01", "D02", 100)])
    assert fs[1]["s_out_cnt_24h"] == 2.0
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "P01", "D01", 100), _ev(2, 87400, "D01", "X01", 50)])
    assert fs[1]["s_in_cnt_24h"] == 0.0
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "P01", "D01", 100), _ev(2, 87399, "D01", "X01", 50)])
    assert fs[1]["s_in_cnt_24h"] == 1.0


def test_receiver_first_credit_and_uniq_transitions(tiny_cfg_session):
    # a second credit from a first-time payer vs a repeat payer
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "P01", "D01", 100), _ev(2, 1100, "P02", "D01", 100)])
    f = fs[1]
    assert f["d_first_credit"] == 0.0
    assert f["d_in_uniq_1h"] == 2.0
    assert f["d_in_uniq_new_1h"] == 2.0
    fs = _run(tiny_cfg_session, [_ev(1, 1000, "P01", "D01", 100), _ev(2, 1100, "P01", "D01", 100)])
    f = fs[1]
    assert f["d_in_uniq_1h"] == 1.0
    assert f["d_in_uniq_new_1h"] == 1.0  # P01 is no longer a first-time payer
    # a payer whose flagged first credit fell out of the 1h window does NOT count as new again:
    # first-contact-ness is recorded once per pair and never re-flags inside the window
    fs = _run(tiny_cfg_session, [_ev(1, 0, "P01", "D01", 100), _ev(2, 3700, "P01", "D01", 100)])
    f = fs[1]
    assert f["d_in_uniq_1h"] == 1.0
    assert f["d_in_uniq_new_1h"] == 0.0  # actual behaviour: the flag lives on the out-of-window credit


# ------------------------------------------------------------------ timestamps
def test_equal_timestamps_follow_stream_order(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [_ev(1, 100, "A00000001", "A00000002", 500), _ev(2, 100, "A00000002", "A00000003", 500)],
    )
    f0, f1 = fs
    assert f0["s_in_cnt_1h"] == 0.0 and f0["s_matched_share"] == 0.0 and f0["g_trail_depth"] == 0.0
    assert f1["s_in_cnt_1h"] == 1.0  # the same-timestamp predecessor counts
    assert f1["s_in_uniq_1h"] == 1.0
    assert f1["s_matched_share"] == 1.0
    assert f1["s_dwell_s"] == 0.0
    assert f1["g_trail_depth"] == 1.0
    # feed order decides, not event_id: reversed stream gives the mirror result
    fs = _run(
        tiny_cfg_session,
        [_ev(2, 100, "A00000002", "A00000003", 500), _ev(1, 100, "A00000001", "A00000002", 500)],
    )
    assert fs[0]["s_in_cnt_1h"] == 0.0 and fs[0]["s_matched_share"] == 0.0
    assert fs[1]["s_in_cnt_1h"] == 0.0


def test_out_of_order_event_is_clamped_to_clock(tiny_cfg_session):
    tz = float(tiny_cfg_session.project.tz_offset_s)
    boundary = 8 * 3600 - tz  # ts where the local hour flips 7 -> 8
    cfg = tiny_cfg_session
    # control: at boundary - 1 the hour is 7
    f = _run(cfg, [_ev(1, boundary - 1, "C01", "C02", 10)])[0]
    assert f["t_hour"] == 7.0
    eng = FeatureEngine(cfg, ("G2",))
    f1 = eng.step(_ev(1, boundary, "A01", "A02", 10))
    assert f1["t_hour"] == 8.0
    f2 = eng.step(_ev(2, boundary - 1, "B01", "B02", 10))  # late by 1 s
    assert f2["t_hour"] == 8.0  # computed at ts_eff = clock, not ev.ts
    f3 = eng.step(_ev(3, 100.0, "C01", "C02", 10))  # very late, far beyond max_lateness_s
    assert f3["t_hour"] == 8.0
    assert eng.clock == boundary
    view = eng.account_view("B01")  # state timestamps use ts_eff too (spec 9.1.8)
    assert view["first_seen"] == boundary and view["last_seen"] == boundary


# ------------------------------------------------------------------ FIFO (spec 9.6)
def test_fifo_partial_consumption(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "S01", 100),
            _ev(2, 10, "S01", "D01", 40),
            _ev(3, 20, "S01", "D01", 100),
            _ev(4, 30, "S01", "D01", 50),
        ],
    )
    f1, f2, f3 = fs[1], fs[2], fs[3]
    # s_matched_share is the share of the DEBIT covered by credits: 40 of the 100 credit -> fully covered
    assert f1["s_matched_share"] == 1.0 and f1["s_dwell_s"] == 10.0 and f1["s_matched_n"] == 1.0
    assert f1["g_trail_depth"] == 1.0 and f1["g_funnel_in"] == 1.0  # fully covered -> relaying
    assert f2["s_matched_share"] == 0.6 and f2["s_dwell_s"] == 20.0 and f2["s_matched_n"] == 1.0
    assert f2["g_trail_depth"] == 1.0  # 0.6 >= 0.5: relaying
    assert f2["s_pass_ratio_1h"] == pytest.approx(1.4)  # (40 + 100) / 100
    assert f3["s_matched_share"] == 0.0  # the credit was emptied and popped
    assert f3["s_dwell_s"] == DWELL_DEFAULT
    assert f3["s_matched_n"] == 0.0
    assert f3["s_pass_ratio_1h"] == pytest.approx(1.9)  # (140 + 50) / 100


def test_fifo_exact_consumption_empties_the_credit(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [_ev(1, 0, "P01", "S01", 100), _ev(2, 10, "S01", "D01", 100), _ev(3, 20, "S01", "D02", 30)],
    )
    assert fs[1]["s_matched_share"] == 1.0 and fs[1]["s_dwell_s"] == 10.0 and fs[1]["s_matched_n"] == 1.0
    assert fs[2]["s_matched_share"] == 0.0 and fs[2]["s_matched_n"] == 0.0
    assert fs[2]["s_dwell_s"] == DWELL_DEFAULT


def test_fifo_expiry_boundary(tiny_cfg_session):
    lookback = float(tiny_cfg_session.features.fifo_lookback_s)
    # inside the lookback, exactly at it (inclusive) and beyond it (expired)
    for ts, matched in ((lookback - 100, True), (lookback, True), (lookback + 1, False)):
        fs = _run(tiny_cfg_session, [_ev(1, 0, "P01", "S01", 100), _ev(2, ts, "S01", "D01", 60)])
        f = fs[1]
        assert f["s_matched_share"] == (1.0 if matched else 0.0), ts
        assert f["s_matched_n"] == (1.0 if matched else 0.0), ts
        if not matched:
            assert f["s_dwell_s"] == DWELL_DEFAULT


def test_fifo_debit_larger_than_all_credits(tiny_cfg_session):
    fs = _run(tiny_cfg_session, [_ev(1, 0, "P01", "S01", 100), _ev(2, 10, "S01", "D01", 250)])
    f = fs[1]
    assert f["s_matched_share"] == 0.4
    assert f["s_dwell_s"] == 10.0 and f["s_matched_n"] == 1.0 and f["s_matched_uniq_src"] == 1.0
    assert f["s_pass_ratio_1h"] == 2.0  # clipped at 2
    assert f["s_pass_ratio_24h"] == 2.0
    assert f["d_pass_ratio_1h"] == 0.0  # receiver has no prior credits


def test_fifo_many_tiny_credits(tiny_cfg_session):
    events = [_ev(i + 1, float(i), f"P{i:02d}", "S01", 0.5) for i in range(10)]
    events += [_ev(11, 20.0, "S01", "D01", 4.2), _ev(12, 30.0, "S01", "D01", 0.5)]
    fs = _run(tiny_cfg_session, events)
    f = fs[10]
    assert f["s_matched_share"] == 1.0
    assert f["s_matched_n"] == 9.0  # eight full credits plus a 0.2 take
    assert f["s_matched_uniq_src"] == 9.0
    takes = [(0.5, 20 - t) for t in range(8)] + [(0.2, 20 - 8)]
    assert f["s_dwell_s"] == pytest.approx(sum(a * d for a, d in takes) / 4.2, abs=1e-9)
    g = fs[11]  # the 0.3 remainder of credit ts=8 plus 0.2 from the untouched credit ts=9
    assert g["s_matched_share"] == 1.0 and g["s_matched_n"] == 2.0
    assert g["s_dwell_s"] == pytest.approx((0.3 * 22 + 0.2 * 21) / 0.5, abs=1e-9)


def test_dwell_is_amount_weighted(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [_ev(1, 0, "P01", "S01", 100), _ev(2, 1000, "P02", "S01", 100), _ev(3, 1000, "S01", "D01", 150)],
    )
    f = fs[2]
    assert f["s_matched_share"] == 1.0
    assert f["s_matched_n"] == 2.0 and f["s_matched_uniq_src"] == 2.0
    assert f["s_dwell_s"] == pytest.approx((100 * 1000 + 50 * 0) / 150, abs=1e-9)


# ------------------------------------------------------------------ trail, funnel (spec 9.7)
def test_trail_depth_four_hop_chain(tiny_cfg_session):
    events = [_ev(i + 1, 10.0 * i, f"A{i:02d}", f"A{i + 1:02d}", 100) for i in range(4)]
    fs = _run(tiny_cfg_session, events)
    assert [f["g_trail_depth"] for f in fs] == [0.0, 1.0, 2.0, 3.0]
    assert [f["g_funnel_in"] for f in fs] == [1.0, 1.0, 1.0, 1.0]
    assert [f["s_matched_share"] for f in fs] == [0.0, 1.0, 1.0, 1.0]
    assert all(f["g_src_susp"] == 0.0 for f in fs)  # one payer only: suspicion rule never fires


def test_trail_depth_capped(tiny_cfg_session):
    cap = int(tiny_cfg_session.features.trail_depth_cap)
    events = [_ev(i + 1, 10.0 * i, f"A{i:02d}", f"A{i + 1:02d}", 100) for i in range(10)]
    fs = _run(tiny_cfg_session, events)
    assert [f["g_trail_depth"] for f in fs] == [float(min(i, cap)) for i in range(10)]


def test_funnel_width_sums_origin_credits(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [_ev(1, 0, "P01", "M01", 100), _ev(2, 1, "P02", "M01", 100), _ev(3, 10, "M01", "C01", 200)],
    )
    f = fs[2]
    assert f["g_funnel_in"] == 2.0
    assert f["g_trail_depth"] == 1.0
    assert f["s_matched_share"] == 1.0 and f["s_matched_uniq_src"] == 2.0
    assert f["s_dwell_s"] == pytest.approx((100 * 10 + 100 * 9) / 200, abs=1e-9)


def test_funnel_width_capped(tiny_cfg_session):
    cfg = tiny_cfg_session.with_overrides({"features.funnel_cap": 2})
    events = [_ev(i + 1, float(i), f"P{i:02d}", "M01", 100) for i in range(3)]
    events.append(_ev(4, 10.0, "M01", "C01", 300))
    fs = _run(cfg, events)
    assert fs[-1]["g_funnel_in"] == 2.0  # capped at funnel_cap


# ------------------------------------------------------------------ suspicion (spec 9.7)
def test_suspicion_flag_set_and_ttl_boundary(tiny_cfg_session):
    ttl = float(tiny_cfg_session.features.susp_ttl_s)
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "S01", 100),
            _ev(2, 10, "P02", "S01", 100),
            _ev(3, 20, "S01", "D01", 200),
            _ev(4, 30, "S01", "D02", 50),
            _ev(5, 20 + ttl - 1, "S01", "D03", 50),
            _ev(6, 20 + ttl, "S01", "D04", 50),
        ],
    )
    assert fs[2]["s_matched_share"] == 1.0 and fs[2]["s_dwell_s"] == 15.0
    assert fs[2]["s_in_uniq_1h"] == 2.0
    assert fs[2]["g_src_susp"] == 0.0  # the flag is set after the read phase of the triggering event
    assert fs[3]["g_src_susp"] == 1.0
    assert fs[4]["g_src_susp"] == 1.0  # one second before expiry
    assert fs[5]["g_src_susp"] == 0.0  # suspicious_until > ts is strict


def test_suspicion_needs_all_three_conditions(tiny_cfg_session):
    # (a) fewer than 2 distinct payers in 1h
    fs = _run(
        tiny_cfg_session,
        [_ev(1, 0, "P01", "S01", 100), _ev(2, 10, "S01", "D01", 100), _ev(3, 20, "S01", "D02", 50)],
    )
    assert fs[2]["g_src_susp"] == 0.0
    # (b) dwell above susp_dwell_max_s
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "S01", 100),
            _ev(2, 0, "P02", "S01", 100),
            _ev(3, 1000, "S01", "D01", 200),
            _ev(4, 1010, "S01", "D02", 50),
        ],
    )
    assert fs[3]["g_src_susp"] == 0.0
    # (b') dwell exactly at the limit still counts (inclusive)
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "S01", 100),
            _ev(2, 0, "P02", "S01", 100),
            _ev(3, 900, "S01", "D01", 200),
            _ev(4, 910, "S01", "D02", 50),
        ],
    )
    assert fs[2]["s_dwell_s"] == 900.0
    assert fs[3]["g_src_susp"] == 1.0
    # (c) matched share below susp_matched_share_min (60 of 100 covered -> 0.6 < 0.7)
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "S01", 30),
            _ev(2, 5, "P02", "S01", 30),
            _ev(3, 10, "S01", "D01", 100),
            _ev(4, 20, "S01", "D02", 50),
        ],
    )
    assert fs[2]["s_matched_share"] == 0.6
    assert fs[3]["g_src_susp"] == 0.0


def test_dst_suspicion_flag(tiny_cfg_session):
    ttl = float(tiny_cfg_session.features.susp_ttl_s)
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "R01", 100),
            _ev(2, 10, "P02", "R01", 100),
            _ev(3, 20, "R01", "X01", 200),
            _ev(4, 30, "Y01", "R01", 50),
            _ev(5, 20 + ttl, "Y02", "R01", 50),
        ],
    )
    assert fs[2]["g_src_susp"] == 0.0
    assert fs[3]["g_dst_susp"] == 1.0
    assert fs[3]["g_src_susp"] == 0.0
    assert fs[4]["g_dst_susp"] == 0.0  # TTL expired exactly


def test_neighbour_suspicion_counts(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "Z01", 100),
            _ev(2, 10, "P02", "Z01", 100),
            _ev(3, 20, "Z01", "W01", 200),  # Z01 becomes suspicious
            _ev(4, 30, "S01", "Z01", 50),  # S01 pays the suspicious Z01
            _ev(5, 40, "Z01", "D01", 50),  # Z01 pays D01
            _ev(6, 50, "S01", "D01", 10),  # probe
            _ev(7, 60, "Z01", "S01", 10),  # suspicious payer of S01
            _ev(8, 70, "S01", "D02", 10),  # probe 2
        ],
    )
    assert fs[3]["g_dst_susp"] == 1.0
    f = fs[5]
    assert f["g_src_susp"] == 0.0 and f["g_dst_susp"] == 0.0
    assert f["g_nbr_out_susp_cnt"] == 1.0  # S01's recent payee Z01 (excluding D01)
    assert f["g_dst_nbr_in_susp_cnt"] == 1.0  # D01's recent payer Z01 (excluding S01)
    assert f["g_nbr_in_susp_cnt"] == 0.0
    assert fs[7]["g_nbr_in_susp_cnt"] == 1.0  # S01's payer Z01 is suspicious


# ------------------------------------------------------------------ flow components (spec 9.7)
def test_hub_never_joins_flow_components(tiny_cfg_session):
    cap = int(tiny_cfg_session.features.hub_degree_cap)
    hub_events = [_ev(i + 1, 10.0 * i, f"P{i:02d}", "H01", 10) for i in range(cap + 1)]
    hub_events += [_ev(32, 400.0, "H01", "D01", 10.0 * (cap + 1)), _ev(33, 500.0, "X01", "H01", 7)]
    fs_hub = _run(tiny_cfg_session, hub_events)
    assert fs_hub[31]["s_in_uniq_1h"] == float(cap + 1)  # 31 distinct payers inside 1 h
    assert fs_hub[31]["s_matched_share"] == 1.0  # the outflow is fully matched: an edge would qualify
    assert (
        fs_hub[31]["g_flow_comp_size"] == 1.0 and fs_hub[31]["g_flow_comp_edges"] == 0.0
    )  # but H01 is a hub
    assert fs_hub[32]["g_flow_comp_size"] == 1.0  # hub edge was never added
    # control: identical flow, only 2 payers -> not a hub -> edge exists
    ctl = [
        _ev(1, 0, "P00", "H01", 155),
        _ev(2, 10, "P01", "H01", 155),
        _ev(3, 400, "H01", "D01", 310),
        _ev(4, 500, "X01", "H01", 7),
    ]
    fs_ctl = _run(tiny_cfg_session, ctl)
    assert fs_ctl[2]["g_flow_comp_size"] == 1.0  # read phase runs before the edge is added
    assert fs_ctl[3]["g_flow_comp_size"] == 2.0 and fs_ctl[3]["g_flow_comp_edges"] == 1.0


def test_flow_components_lifecycle(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "R01", "A01", 100),  # R01 unfunded: share 0, no edge
            _ev(2, 1000, "A01", "B01", 100),  # fully matched: edge A01-B01 added after the read phase
            _ev(3, 1100, "B01", "C01", 100),  # edge B01-C01 added
            _ev(4, 1200, "B01", "E01", 10),  # B01's credit was consumed: share 0, no new edge
            _ev(5, 4800, "Q01", "Z01", 50),
        ],
    )
    assert [(f["g_flow_comp_size"], f["g_flow_comp_edges"]) for f in fs] == [
        (1.0, 0.0),  # first event: no edges yet
        (1.0, 0.0),  # read phase runs before the A01-B01 edge is added
        (2.0, 1.0),  # B01 sees the A01-B01 edge
        (3.0, 2.0),  # B01 sees both edges
        (1.0, 0.0),  # rebuild at t=4800 (tick 16, cutoff 1200) dropped both edges
    ]


def test_flow_rebuild_drops_edges_at_or_before_cutoff(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "R01", "A01", 100),
            _ev(2, 1200, "A01", "B01", 100),  # edge exactly at the future cutoff -> dropped
            _ev(3, 1201, "R02", "C01", 100),
            _ev(4, 1201, "C01", "D01", 100),  # one second after the cutoff -> kept
            _ev(5, 4800, "P01", "A01", 7),  # triggers rebuild: tick_time 4800, cutoff 4800-3600
            _ev(6, 4801, "P02", "C01", 7),  # same tick, no rebuild
        ],
    )
    assert fs[4]["g_flow_comp_size"] == 1.0  # A01-B01 edge dropped (ts == cutoff is dropped)
    assert fs[5]["g_flow_comp_size"] == 2.0  # C01-D01 edge kept
    assert fs[5]["g_flow_comp_edges"] == 1.0


# ------------------------------------------------------------------ caps (spec 9.1 item 3)
def test_deque_cap_saturates_window_counters(tiny_cfg_session):
    cfg = tiny_cfg_session.with_overrides({"features.deque_cap": 3})
    events = [_ev(i + 1, float(i), "S01", f"D{i:02d}", 1000.0 + 10 * i) for i in range(10)]
    events.append(_ev(11, 10.0, "S01", "DXXX", 1100.0))
    f = _run(cfg, events)[-1]
    assert f["s_out_cnt_1h"] == 4.0  # deque_cap stored + current
    assert f["s_out_uniq_1h"] == 4.0
    assert f["s_out_amt_1h"] == 1070.0 + 1080.0 + 1090.0 + 1100.0
    # receiver side
    events = [_ev(i + 1, float(i), f"P{i:02d}", "R01", 10) for i in range(10)]
    events.append(_ev(11, 10.0, "PXXX", "R01", 10))
    f = _run(cfg, events)[-1]
    assert f["d_in_cnt_1h"] == 4.0
    assert f["d_in_uniq_1h"] == 4.0
    assert f["d_in_uniq_new_1h"] == 4.0
    assert f["d_in_amt_1h"] == 40.0
    # control: without the cap the same streams count everything
    events = [_ev(i + 1, float(i), "S01", f"D{i:02d}", 10) for i in range(10)]
    events.append(_ev(11, 10.0, "S01", "DXXX", 10))
    f = _run(tiny_cfg_session, events)[-1]
    assert f["s_out_cnt_1h"] == 11.0 and f["s_out_uniq_1h"] == 11.0


def test_payee_memory_lru_eviction(tiny_cfg_session):
    stream = [_ev(1, 0, "A01", "P01", 10), _ev(2, 10, "A01", "P02", 10), _ev(3, 20, "A01", "P01", 10)]
    assert _run(tiny_cfg_session, stream)[2]["s_new_payee"] == 0.0  # P01 is remembered
    cfg = tiny_cfg_session.with_overrides({"features.payee_memory_n": 2})
    fs = _run(cfg, stream[:2] + [_ev(3, 20, "A01", "P03", 10), _ev(4, 30, "A01", "P01", 10)])
    assert fs[2]["s_new_payee"] == 1.0  # P01 was pushed out of the 2-entry LRU
    assert fs[3]["s_new_payee"] == 1.0  # ... so paying it again looks new
    # receiving from P01 does not count as having paid it
    fs = _run(tiny_cfg_session, [_ev(1, 0, "P01", "A01", 10), _ev(2, 10, "A01", "P01", 10)])
    assert fs[1]["s_new_payee"] == 1.0


def test_amount_history_median_and_cap(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [_ev(1, 0, "S01", "D01", 100), _ev(2, 10, "S01", "D01", 200), _ev(3, 20, "S01", "D01", 400)],
    )
    assert fs[0]["s_amt_vs_median"] == 1.0  # no history yet
    assert fs[1]["s_amt_vs_median"] == 2.0  # 200 / median([100])
    assert fs[2]["s_amt_vs_median"] == pytest.approx(400 / 150, abs=1e-9)
    fs = _run(tiny_cfg_session, [_ev(1, 0, "S01", "D01", 100), _ev(2, 10, "S01", "D01", 100000)])
    assert fs[1]["s_amt_vs_median"] == 50.0  # clipped
    cfg = tiny_cfg_session.with_overrides({"features.amount_history_n": 2})
    events = [_ev(i + 1, 10.0 * i, "S01", "D01", a) for i, a in enumerate([100, 200, 400, 800])]
    f = _run(cfg, events)[-1]
    assert f["s_amt_vs_median"] == pytest.approx(800 / 300, abs=1e-9)  # median of the last 2 only


# ------------------------------------------------------------------ eviction (spec 9.4)
def test_eviction_resets_only_heavy_state(tiny_cfg_session):
    ttl = float(tiny_cfg_session.features.state_ttl_s)
    eng = FeatureEngine(tiny_cfg_session, ("G2",))
    eng.step(_ev(1, 900, "P01", "S01", 1000))
    f2 = eng.step(_ev(2, 1000, "S01", "T01", 500))
    assert f2["s_matched_share"] == 1.0 and f2["s_dwell_s"] == 100.0  # the 1000 credit covers the 500 debit
    assert f2["s_dwell_ewma_s"] == 86400.0  # EWMA still at its default before the update
    jump = 1000 + ttl + 4000
    f3 = eng.step(_ev(3, jump, "Q01", "Z01", 50))
    assert f3 is not None
    stats = eng.stats(exact=True)
    assert stats["evicted_states"] == 3  # P01, S01 and T01 idle longer than state_ttl_s
    view = eng.account_view("S01")
    assert view["state_active"] is False
    assert view["first_seen"] == 900 and view["last_seen"] == 1000  # the light registry survives
    f4 = eng.step(_ev(4, jump + 1, "S01", "T02", 700))
    assert f4["s_new_payee"] == 1.0  # T01 forgotten: heavy memory was reset
    assert f4["s_dwell_ewma_s"] == DWELL_DEFAULT  # reset to dwell_default_s
    assert f4["s_amt_vs_median"] == 1.0  # amount history gone (700/500 would be 1.4)
    assert f4["s_age_s"] == (jump + 1) - 900  # ages still come from the light registry
    assert f4["s_idle_s"] == (jump + 1) - 1000
    assert eng.stats(exact=True)["events"] == 4


# ------------------------------------------------------------------ E and D history groups
def test_e_group_pair_memory(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "B01", "A01", 100),
            _ev(2, 10, "A01", "B01", 50),
            _ev(3, 20, "A01", "B01", 100),
            _ev(4, 30, "A01", "B01", 25),
            _ev(5, 90000, "A01", "B01", 50),
        ],
    )
    f = fs[1]
    assert f["e_pair_out_cnt"] == 0.0 and f["e_amt_vs_pair_mean"] == 1.0
    assert f["e_reverse_recent"] == 1.0  # A01 received from B01 within 24h
    assert fs[2]["e_pair_out_cnt"] == 1.0 and fs[2]["e_amt_vs_pair_mean"] == 2.0  # 100 / 50
    assert fs[3]["e_pair_out_cnt"] == 2.0
    assert fs[3]["e_amt_vs_pair_mean"] == pytest.approx(25 / 75, abs=1e-9)
    assert fs[4]["e_reverse_recent"] == 0.0  # the reverse payment is older than 24h


def test_d_pass_ratio_uses_prior_state_and_clips(tiny_cfg_session):
    fs = _run(
        tiny_cfg_session,
        [
            _ev(1, 0, "P01", "D01", 100),
            _ev(2, 10, "D01", "X01", 300),  # paid from its own money
            _ev(3, 20, "P02", "D01", 1000),  # the credit event itself sees only prior sums
            _ev(4, 30, "D01", "Y01", 10),
            _ev(5, 40, "P03", "D01", 5),  # probe with D01 as receiver again
        ],
    )
    assert fs[1]["d_out_cnt_1h"] == 0.0 and fs[1]["d_pass_ratio_1h"] == 0.0
    assert fs[2]["d_in_cnt_1h"] == 2.0 and fs[2]["d_in_uniq_1h"] == 2.0  # (+cur) current credit counted
    assert fs[2]["d_pass_ratio_1h"] == 2.0  # prior out 300 / prior in 100 -> clipped at 2
    assert fs[3]["d_out_cnt_1h"] == 0.0  # event 4's receiver Y01 never debited
    assert fs[4]["d_out_cnt_1h"] == 2.0 and fs[4]["d_out_uniq_1h"] == 2.0  # X01 and Y01 debits
    assert fs[4]["d_pass_ratio_1h"] == pytest.approx(310 / 1100, abs=1e-9)  # (300 + 10) / (100 + 1000)


# ------------------------------------------------------------------ engine plumbing
def test_engine_column_sets_and_reset(tiny_cfg_session):
    eng0 = FeatureEngine(tiny_cfg_session, ("B0",))
    assert eng0.names == feature_names("B0")
    assert eng0.use_graph is False
    f = eng0.step(_ev(1, 0, "S01", "D01", 10))
    assert set(f) == set(ALL_NAMES)  # the dict always carries every feature
    vec = eng0.vector(f, eng0.names)
    assert isinstance(vec, list) and len(vec) == 13
    eng_both = FeatureEngine(tiny_cfg_session, ("B0", "B1"))
    assert eng_both.names == feature_names("B1")  # union, canonical order
    with pytest.raises(ValueError):
        FeatureEngine(tiny_cfg_session, ("B9",))
    # reset() reproduces the same features for the same stream
    eng_r = FeatureEngine(tiny_cfg_session, ("B0",))
    stream = [_ev(1, 0, "P01", "S01", 100), _ev(2, 10, "S01", "D01", 40)]
    first = [eng_r.step(e) for e in stream]
    eng_r.reset()
    second = [eng_r.step(e) for e in stream]
    assert first == second
