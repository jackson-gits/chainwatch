"""Evaluation metrics (spec 14.3, 14.4): PR-AUC, ROC-AUC, recall_at_fpr, wilson, ring_outcomes,
false_rings, archetype_fpr, bootstrap."""
from __future__ import annotations

import math

import numpy as np
import pytest

from chainwatch.eval import metrics as M


def test_average_precision_perfect_and_worst():
    y = np.array([1, 1, 0, 0])
    s = np.array([0.9, 0.8, 0.1, 0.05])
    ap = M.average_precision(y, s)
    assert 0 <= ap <= 1
    y2 = np.array([1, 1, 0])
    s2 = np.array([0.1, 0.2, 0.9])
    assert M.average_precision(y2, s2) < 0.7
    assert math.isnan(M.average_precision(np.array([0, 0]), np.array([0.5, 0.5])))


def test_roc_auc_basic():
    y = np.array([1, 0, 1, 0])
    s = np.array([0.8, 0.2, 0.7, 0.3])
    auc = M.roc_auc(y, s)
    assert 0.5 <= auc <= 1.0
    assert math.isnan(M.roc_auc(np.array([0, 0]), np.array([0.1, 0.2])))
    assert math.isnan(M.roc_auc(np.array([1, 1]), np.array([0.1, 0.2])))


def test_recall_at_fpr_curve_optimistic():
    np.random.seed(7)
    y = np.array([1] * 10 + [0] * 100)
    s = np.concatenate([np.linspace(0.8, 0.5, 10), np.linspace(0.4, 0.1, 100)])
    res = M.recall_at_fpr_curve(y, s, 0.05)
    assert res["fpr"] is not None
    assert res["recall"] is not None
    assert res["threshold"] is not None
    assert res["recall"] <= 1.0
    res2 = M.recall_at_fpr_curve(np.array([1, 0]), np.array([0.9, 0.1]), 0.01)
    assert res2["recall"] is not None


def test_wilson_interval_bounds():
    lo, hi = M.wilson(5, 100, z=1.959964)
    assert lo is not None and hi is not None
    assert 0 <= lo <= hi <= 1
    assert M.wilson(0, 0) == (None, None)


def test_transaction_metrics_shape():
    y = np.array([1, 0, 1, 0])
    s = np.array([0.9, 0.1, 0.8, 0.2])
    tier = np.array([2, 0, 1, 0])
    tm = M.transaction_metrics(y, s, tier, [0.01, 0.05])
    assert set(tm) == {"n", "n_pos", "n_neg", "pr_auc", "roc_auc", "deployed", "curve", "alerts_per_10k"}
    # tier>=1 flags exactly indices 0 and 2, which are exactly the two positives -> recall 1.0
    assert tm["deployed"]["recall"]["p"] == 1.0
    assert tm["alerts_per_10k"]["hold"] == 2500.0
    assert tm["alerts_per_10k"]["verify"] == 2500.0


def test_ring_outcomes_catches_when_min_accounts_met():
    rings = [
        {
            "ring_id": "R1",
            "typology": "chain",
            "n_accounts": 4,
            "first_ts": 100.0,
            "exit_ts": 1000.0,
            "exit_hop": 3,
            "accounts": {"mules": ["M1", "M2"], "collectors": ["C1"]},
        }
    ]
    stream = {
        "ts": np.array([100, 200, 300, 400]),
        "src": ["M1", "X", "M2", "Y"],
        "dst": ["A", "A", "C1", "Z"],
        "ring_id": np.array([5, -1, 5, -1]),
        "hop": np.array([1, 0, 2, 0]),
        "dst_role": np.array([1, 0, 3, 0]),
        "amount": np.array([10, 5, 20, 1]),
    }
    tier = np.array([2, 0, 2, 0])
    out = M.ring_outcomes(rings, stream, tier, 50, 500, min_accounts=2, min_tier=1)
    assert len(out) == 1
    assert out[0]["caught"] is True
    # M1 appears at i=0 (ts=100), but only one member so far; M2/C1 appear at i=2 (ts=300),
    # which brings distinct members to >= 2, so tau is set to ts=300.0
    assert out[0]["tau"] == 300.0
    assert out[0]["ttc_min"] == pytest.approx((300.0 - 100.0) / 60.0)  # ~3.333 min


def test_summarize_rings_empty():
    s = M.summarize_rings([])
    assert s["n_rings"] == 0


def test_false_rings_counts():
    log_ = {
        "R1": {
            "alert_ts": 200.0,
            "event_ids": [1, 2],
            "accounts": ["A1", "A2"],
            "peak": 0.9,
        },
        "R2": {
            "alert_ts": 300.0,
            "event_ids": [3, 4],
            "accounts": ["L1", "L2"],
            "peak": 0.8,
        },
    }
    label_of = {1: 0, 2: 0, 3: 1, 4: 1}
    res = M.false_rings(log_, label_of, {"L1", "L2"}, 100.0, 400.0, purity_min=0.5, days=0.5)
    assert res["alerts"] == 2
    assert res["false"] == 1  # R1 has purity 0.0 < 0.5


def test_archetype_fpr_aggregates():
    stream = {
        "src": ["L1", "L1"],
        "dst": ["L2", "M1"],
        "ts": np.array([0.0, 86400.0]),
    }
    y = np.array([0, 0])
    tier = np.array([1, 0])
    archetype_of = {"L1": "agent", "L2": "trader"}
    mule = {"M1"}
    res = M.archetype_fpr(stream, y, tier, archetype_of, mule, days=1.0)
    assert "agent" in res
    assert res["agent"]["accounts_alerted"] == 1
    assert res["agent"]["accounts_active"] >= 1


def test_bootstrap_ci_empty():
    assert M.bootstrap_ci(np.array([]), np.mean, 100, 7) == [None, None]


def test_bootstrap_ci_basic():
    vals = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    ci = M.bootstrap_ci(vals, np.mean, 50, 7)
    assert ci[0] is not None
    assert ci[0] <= ci[1]
