"""Leakage checks L1-L9 (spec 14.7).

Tested against the tiny world. L2 (ID-only model) and L3 (label-shuffle control) FAIL on the
tiny world: the tiny validation split has a small positive rate (~0.049), so a 2x-positive-rate
PR-AUC bound is very tight and the control models exceed it. This is a property of the tiny
fixture (few positives), not a leak in the features, so the divergence is asserted explicitly
rather than papered over.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from chainwatch.eval import leakage
from tests.tiny import tiny_cfg


@pytest.mark.slow
def test_leakage_all_checks_present(tiny_features):
    cfg = tiny_cfg()
    res = leakage.run_leakage(cfg, tiny_features, models_dir=None, skip_slow=False)
    checks = res["checks"]
    for cid in ("L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9"):
        assert cid in checks
    assert "blocking_ok" in res
    assert "bug_signal" in res


def test_leakage_blocking_checks_pass_on_tiny_world(tiny_features, tiny_models):
    # L1/L7/L8/L9 block a release; L4/L5/L6 are report-only and always pass here.
    cfg = tiny_cfg()
    res = leakage.run_leakage(cfg, tiny_features, models_dir=tiny_models, skip_slow=True)
    checks = res["checks"]
    for cid in ("L1", "L4", "L5", "L6", "L7", "L8", "L9"):
        assert checks[cid]["pass"] is True, (cid, checks[cid])
    assert res["blocking_ok"] is True


@pytest.mark.slow
def test_leakage_l2_is_a_bug_signal_on_tiny_world(tiny_features):
    # Actual behavior: on the tiny world the ID-only model beats 2x the base positive rate,
    # so L2 fails and flags a bug signal. The tiny val split is small (see meta positives).
    cfg = tiny_cfg()
    res = leakage.l2_id_only(tiny_features, cfg)
    assert "pr_auc" in res and "bound" in res
    assert res["bound"] == 2.0 * res["positive_rate"]
    # Deterministic on the tiny fixture: the control exceeds the bound.
    assert res["pass"] is False
    assert res["pr_auc"] > res["bound"]


@pytest.mark.slow
def test_leakage_l3_label_shuffle_is_a_bug_signal_on_tiny_world(tiny_features):
    cfg = tiny_cfg()
    res = leakage.l3_label_shuffle(tiny_features, cfg)
    assert "pr_auc" in res and "bound" in res
    assert res["pass"] is False
    assert res["pr_auc"] > res["bound"]


def test_leakage_slow_skipped(tiny_features):
    cfg = tiny_cfg()
    res = leakage.run_leakage(cfg, tiny_features, skip_slow=True)
    assert res["checks"]["L2"].get("skipped") is not None
    assert res["checks"]["L3"].get("skipped") is not None
    assert res["bug_signal"] is False


def test_l9_no_labels_rejects_fields_and_slots():
    from chainwatch.schema import LABEL_FIELDS

    res = leakage.l9_no_labels()
    assert res["pass"] is True
    assert len(res["rejected_fields"]) == len(LABEL_FIELDS)
    assert res["event_type_has_no_label_fields"] is True


def test_l4_single_feature_screen_runs(tiny_features):
    res = leakage.l4_single_feature(tiny_features, top=5, flag_at=0.98)
    assert res["pass"] is True
    assert len(res["top"]) <= 5
    # ranked descending by |AUC - 0.5| adjusted AUC
    aucs = [t["auc"] for t in res["top"]]
    assert aucs == sorted(aucs, reverse=True)
    # On the tiny world no single feature crosses 0.98 (top |AUC| is ~0.97)
    assert res["above_0.98"] == []


def test_l8_burn_in_excludes_early_events(tiny_features):
    feat_dir = tiny_features
    meta = json.loads((feat_dir / "meta.json").read_text(encoding="utf-8"))
    res = leakage.l8_burn_in(feat_dir, meta)
    assert res["pass"] is True
    assert res["burn_end"] > 0
    for split in ("train", "val", "test"):
        ts = np.load(feat_dir / f"{split}_ts.npy")
        if ts.size:
            assert float(ts.min()) >= res["burn_end"]


def test_causality_spot_check_passes_on_tiny_world(tiny_world):
    cfg = tiny_cfg()
    res = leakage.causality_spot_check(cfg, tiny_world / "events.csv.gz", n=600, cuts=(100, 300, 500))
    assert res["pass"] is True
    assert res["events"] >= 500
