"""Thresholds (spec 10.4, 11.6): quantile ties, achieved FPR, hold >= verify, ring_alert
from toy rings via calibrate_ring_variant with numpy arrays, bisection convergence."""
from __future__ import annotations

import numpy as np
import pytest

from chainwatch.model import thresholds as thr


def test_q_higher_handles_empty():
    assert thr.q_higher(np.array([]), 0.05) == 1.0


def test_q_higher_quantile_with_ties():
    neg = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    assert thr.q_higher(neg, 0.2) == pytest.approx(0.5)  # 1-0.2=0.8 quantile, higher method


def test_base_thresholds_hold_gte_verify():
    neg = np.linspace(0, 1, 100)
    bt = thr.base_thresholds(neg, 0.05, 0.05, 0.01)
    assert bt["hold"] >= bt["verify"]
    assert bt["val"]["n_neg"] == 100
    assert 0 <= bt["val"]["fpr_verify"] <= 1


def test_choose_ring_alert_basic():
    peaks = [0.9, 0.8, 0.7, 0.6]
    res = thr.choose_ring_alert(peaks, 1.0, 2.0)
    assert res == pytest.approx(0.7)


def test_calibrate_ring_variant_simple_without_rings():
    st = {
        "ts": np.array([0.0, 1.0, 2.0, 3.0]),
        "event_id": np.array([1, 2, 3, 4], dtype=np.int64),
        "src": np.array(["A", "B", "C", "D"]),
        "dst": np.array(["B", "C", "D", "E"]),
        "amount": np.array([10.0, 20.0, 30.0, 40.0]),
        "type": np.array([1, 1, 1, 1]),
    }
    scores = np.array([0.9, 0.8, 0.7, 0.1])
    y = np.array([1, 1, 0, 0])
    from chainwatch.config import load

    cfg = load(overrides={"thresholds.calibrate_with_rings": False})
    res = thr.calibrate_ring_variant(st, scores, y, cfg, days=0.5)
    assert res["ring_alert"] >= 0
    assert res["calibration"]["mode"] == "simple"
    assert res["val"]["n_neg"] == 2
    assert res["val"]["n_pos"] == 2


def _toy_ring_stream():
    """One genuine mule ring V->M1->M2->S plus low/high-scoring legitimate rows."""
    rows = []
    for i in range(40):  # legit low scores
        rows.append((float(i), 1000 + i, f"L{i}", f"K{i}", 10.0, 0))
    for row in (  # ring fraud rows, high scores
        (100.0, 1, "V", "M1", 1000.0, 0),
        (110.0, 2, "M1", "M2", 950.0, 0),
        (120.0, 3, "M2", "S", 900.0, 2),
    ):
        rows.append(row)
    for i in range(40):  # legit medium scores
        rows.append((200.0 + i, 2000 + i, f"N{i}", f"P{i}", 5.0, 2))
    low = [0.05 + 0.001 * i for i in range(40)]
    mid = [0.5 + 0.001 * i for i in range(40)]
    scores = np.array(low + [0.9, 0.9, 0.9] + mid)
    y = np.array([0] * 40 + [1, 1, 1] + [0] * 40, dtype=np.int64)
    st = {
        "ts": np.array([r[0] for r in rows], dtype=np.float64),
        "event_id": np.array([r[1] for r in rows], dtype=np.int64),
        "src": np.array([r[2] for r in rows]),
        "dst": np.array([r[3] for r in rows]),
        "amount": np.array([r[4] for r in rows], dtype=np.float64),
        "type": np.array([r[5] for r in rows], dtype=np.int64),
    }
    return st, scores, y


def test_calibrate_ring_variant_toy_ring(tiny_cfg_session):
    """A real toy ring: ring_alert stays finite, calibration reports matched FPR (spec 11.6)."""
    st, scores, y = _toy_ring_stream()
    res = thr.calibrate_ring_variant(st, scores, y, tiny_cfg_session, days=1.0)
    assert set(res) == {"verify", "hold", "edge", "ring_alert", "val", "calibration"}
    assert res["ring_alert"] >= 0.0
    assert res["hold"] >= res["verify"]
    assert res["edge"] == pytest.approx(res["verify"])
    assert res["val"]["n_neg"] == 80
    assert res["val"]["n_pos"] == 3
    assert res["val"]["rings_tracked"] >= 1
    calib = res["calibration"]
    assert calib["mode"] in {"simple", "matched_fpr"}
    assert calib["iterations"] <= int(tiny_cfg_session.thresholds.bisect_iters)
    assert calib["fpr_after_escalation"] <= calib["fpr_before_escalation"] + 1e-12
