"""Evaluation protocol (@slow): evaluate_core on tiny world, variants produce results,
test-touch log blocks rerun, headline sentences render. Must run under ~3 minutes."""
from __future__ import annotations

import json

import pytest

from chainwatch.eval import protocol
from tests.tiny import tiny_cfg


@pytest.mark.slow
def test_evaluate_core_produces_all_variants(tiny_features, tiny_models, tmp_workdir):
    cfg = tiny_cfg()
    res = protocol.evaluate_core(cfg, tiny_features, tiny_models)
    assert "transaction_level" in res
    assert "ring_level" in res
    assert "archetype_fpr" in res
    assert "operating_points" in res
    assert "units" in res
    assert "meta" in res
    for v in protocol.VARIANTS:
        assert v in res["transaction_level"]
        assert v in res["ring_level"]
    hl = protocol.headline(res)
    for v in ("B0", "B1", "B1+R", "G2", "G2+R"):
        assert v in hl


@pytest.mark.slow
def test_evaluate_writes_output_and_touch_log(tiny_features, tiny_models, tmp_workdir):
    cfg = tiny_cfg()
    from chainwatch.config import ROOT

    res = protocol.evaluate(cfg, tiny_features, tiny_models, allow_rerun=False)
    out = ROOT / "results" / f"eval_{res['meta']['world']}.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["world"] == res["meta"]["world"]
    assert protocol.TOUCH_LOG.exists()


@pytest.mark.slow
def test_check_touch_blocks_rerun(tiny_features, tiny_models):
    cfg = tiny_cfg()
    protocol.evaluate(cfg, tiny_features, tiny_models, allow_rerun=True)  # first
    with pytest.raises(protocol.TestTouchRefused):
        protocol.evaluate(cfg, tiny_features, tiny_models, allow_rerun=False)  # second blocked
