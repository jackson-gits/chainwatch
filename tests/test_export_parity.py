"""Export parity (@slow): train small LightGBM on tiny_features, export, compare PortableGBDT
vs LightGBM on sampled rows (prob diff <1e-7, identical leaves)."""
from __future__ import annotations

import numpy as np
import pytest

from chainwatch.model import export, vector_predict


@pytest.mark.slow
def test_export_parity_vs_lightgbm(tiny_features, tmp_workdir):
    try:
        import lightgbm as lgb
    except Exception:
        pytest.skip("lightgbm not available")

    from chainwatch.features.matrix import load_split
    from chainwatch.model.portable_gbdt import PortableGBDT

    tr = load_split(tiny_features, "train")
    va = load_split(tiny_features, "val")
    Xtr = np.asarray(tr["X"], dtype=np.float64)
    ytr = np.asarray(tr["y"], dtype=int)
    Xva = np.asarray(va["X"], dtype=np.float64)

    params = {
        "objective": "binary",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "min_child_samples": 50,
        "deterministic": True,
        "verbosity": -1,
        "num_threads": 1,
    }
    model = lgb.train(params, lgb.Dataset(Xtr, ytr, feature_name=list(tr["names"])), num_boost_round=20)
    portable = export.booster_to_portable(
        model,
        detector="G2",
        feature_names=list(tr["names"]),
        registry_hash="sha256:test",
        num_iteration=model.num_trees(),
        trained_on={"world": "tiny"},
    )
    ppath = tmp_workdir / "g2_export.json"
    export.write_model(portable, ppath)

    pg = PortableGBDT.load(ppath)
    vg = vector_predict.VectorGBDT.load(ppath)

    rng = np.random.default_rng(7)
    idx = rng.choice(Xva.shape[0], size=min(5000, Xva.shape[0]), replace=False)
    Xs = Xva[idx]
    n_iter = model.num_trees()
    lp = model.predict(Xs, num_iteration=n_iter)
    pp = pg.predict_many(Xs)
    vp = vg.predict(Xs)
    for a, b, c in zip(lp, pp, vp, strict=False):
        assert abs(a - b) < 1e-7
        assert abs(a - c) < 1e-7

    if Xs.shape[0] > 0:
        pg.check_features(tr["names"])
        ll = model.predict(Xs, pred_leaf=True, num_iteration=n_iter)
        pl = np.array([pg.leaf_indices(row) for row in Xs.tolist()])
        assert ll.shape == pl.shape
        assert (ll == pl).all()


@pytest.mark.slow
def test_export_parity_round_trip():
    try:
        import lightgbm as lgb
    except Exception:
        pytest.skip("lightgbm not available")

    from chainwatch.model import portable_gbdt

    X = np.array([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6], [0.7, 0.8]])
    y = np.array([0, 1, 0, 1])
    params = {"objective": "binary", "verbosity": -1, "min_data_in_leaf": 1}
    model = lgb.train(params, lgb.Dataset(X, y, feature_name=["f0", "f1"]), num_boost_round=3)
    n_iter = model.num_trees()
    portable = export.booster_to_portable(
        model,
        detector="T",
        feature_names=["f0", "f1"],
        registry_hash="sha256:t",
        num_iteration=n_iter,
        trained_on={"world": "t"},
    )
    assert portable["objective"] == "binary"
    assert portable["format"] == portable_gbdt.FORMAT
    pg = portable_gbdt.PortableGBDT(portable)
    assert pg.n_trees == portable["n_trees"] >= 1
    for i in range(X.shape[0]):
        expected = float(model.predict(X[i : i + 1], num_iteration=n_iter)[0])
        assert pg.predict(X[i].tolist()) == pytest.approx(expected, abs=1e-7)


def test_export_rejects_categorical_decision_type_and_missing_type():
    struct = {
        "split_index": 0,
        "split_feature": 0,
        "threshold": "0||1",
        "decision_type": "==",
        "missing_type": "None",
        "left_child": {"leaf_index": 0, "leaf_value": 0.0},
        "right_child": {"leaf_index": 1, "leaf_value": 1.0},
    }
    with pytest.raises(export.ExportError, match="decision_type"):
        export._tree_arrays(struct)

    struct2 = dict(struct, decision_type="<=", missing_type="Zero")
    with pytest.raises(export.ExportError, match="missing_type"):
        export._tree_arrays(struct2)

    struct3 = dict(struct, decision_type="<=", missing_type="None")
    with pytest.raises(export.ExportError, match="categorical threshold"):
        export._tree_arrays(struct3)


def test_export_rejects_non_binary_objective_and_wrong_feature_names():
    class _Stub:
        def __init__(self, dump):
            self._dump = dump

        def dump_model(self, num_iteration=None):
            return self._dump

    base_tree = {
        "split_index": 0,
        "split_feature": 0,
        "threshold": 0.5,
        "decision_type": "<=",
        "missing_type": "None",
        "left_child": {"leaf_index": 0, "leaf_value": 0.0},
        "right_child": {"leaf_index": 1, "leaf_value": 1.0},
    }
    dump = {
        "objective": "regression",
        "feature_names": ["f0"],
        "tree_info": [{"tree_structure": base_tree}],
    }
    with pytest.raises(export.ExportError, match="binary objective"):
        export.booster_to_portable(_Stub(dump), "T", ["f0"], "sha256:t", 1, {"world": "t"})

    dump2 = {"objective": "binary", "feature_names": ["Column_0"], "tree_info": []}
    with pytest.raises(export.ExportError, match="feature names differ"):
        export.booster_to_portable(_Stub(dump2), "T", ["f0"], "sha256:t", 1, {"world": "t"})


def test_export_rejects_non_contiguous_node_numbering():
    # LightGBM numbers internal nodes and leaves from 0; missing index 1 must be rejected
    struct = {
        "split_index": 2,
        "split_feature": 0,
        "threshold": 0.5,
        "decision_type": "<=",
        "missing_type": "None",
        "left_child": {"leaf_index": 5, "leaf_value": 0.0},
        "right_child": {"leaf_index": 6, "leaf_value": 1.0},
    }
    with pytest.raises(export.ExportError, match="unexpected node or leaf numbering"):
        export._tree_arrays(struct)

