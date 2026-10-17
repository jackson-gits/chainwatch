"""Portable GBDT evaluator (spec 10.3, 17.2): hand-built trees, boundary semantics, leaf encoding,
single-leaf trees, sigmoid numerics and rejection of malformed models. Pure Python, no numpy."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from chainwatch.model.portable_gbdt import (
    FORMAT,
    ModelFormatError,
    PortableGBDT,
    file_sha256,
    sigmoid,
)


def _tree(feature, threshold, left, right, leaf, root_leaf: bool = False) -> dict:
    t: dict = {"feature": feature, "threshold": threshold, "left": left, "right": right, "leaf": leaf}
    if root_leaf:
        t["root_leaf"] = True
    return t


def _model(trees: list[dict], names: list[str] | None = None, **extra) -> dict:
    data = {
        "format": FORMAT,
        "detector": "TEST",
        "feature_names": names if names is not None else ["f0", "f1"],
        "registry_hash": "sha256:test",
        "objective": "binary",
        "sigmoid": 1.0,
        "n_trees": len(trees),
        "trees": trees,
    }
    data.update(extra)
    return data


# ------------------------------------------------------------------ hand-built trees
def test_simple_split_predict_raw_and_probability():
    # node 0: f0 <= 0.5 -> node 1, else leaf 2 (-0.4). node 1: f1 <= 0.3 -> leaf 0 (0.1), else leaf 1 (0.2)
    tree = _tree([0, 1], [0.5, 0.3], [1, -1], [-3, -2], [0.1, 0.2, -0.4])
    m = PortableGBDT(_model([tree]))
    assert m.predict_raw([0.4, 0.2]) == pytest.approx(0.1)  # left at root -> node1, 0.2<=0.3 -> leaf0
    assert m.predict_raw([0.4, 0.9]) == pytest.approx(0.2)  # left at root -> node1, 0.9>0.3 -> leaf1
    assert m.predict_raw([0.6, 0.9]) == pytest.approx(-0.4)  # right at root -> leaf2
    assert m.predict([0.4, 0.2]) == pytest.approx(sigmoid(0.1))


def test_boundary_equal_threshold_goes_left():
    # spec 10.3: node i tests x[feature[i]] <= threshold[i]; equality goes left
    tree = _tree([0], [0.5], [-1], [-2], [1.0, -1.0])
    m = PortableGBDT(_model([tree]))
    assert m.predict_raw([0.5]) == pytest.approx(1.0)
    assert m.predict_raw([math.nextafter(0.5, math.inf)]) == pytest.approx(-1.0)
    assert m.predict_raw([0.4999]) == pytest.approx(1.0)


def test_negative_child_leaf_encoding():
    # child value c < 0 means leaf number ~c: -1 is leaf 0, -2 is leaf 1, -3 is leaf 2
    tree = _tree([0, 0], [1.0, 2.0], [1, -1], [-3, -2], [10.0, 20.0, 30.0])
    m = PortableGBDT(_model([tree]))
    # node0: f0 <= 1.0 -> node1, else leaf ~(-3)=2 (30.0)
    assert m.predict_raw([0.0]) == pytest.approx(10.0)
    assert m.predict_raw([3.0]) == pytest.approx(30.0)
    assert m.predict_raw([1.5]) == pytest.approx(30.0)
    # node1: f0 <= 2.0 -> leaf ~(-1)=0 (10.0), else leaf ~(-2)=1 (20.0)
    tree2 = _tree([0, 0], [1.0, 2.0], [1, -1], [-3, -2], [10.0, 20.0, 30.0])
    m2 = PortableGBDT(_model([tree2]))
    assert m2.predict_raw([0.5]) == pytest.approx(10.0)  # left to node1, 0.5 <= 2.0 -> leaf0
    assert m2.predict_raw([2.5]) == pytest.approx(30.0)  # root false -> leaf2 (node1 unreachable)
    assert m.leaf_indices([0.0]) == [0]
    assert m.leaf_indices([3.0]) == [2]
    assert m.leaf_indices([1.5]) == [2]
    # tree where node1 is reached via node0 left, then node1 right -> leaf1
    tree3 = _tree([0, 0], [1.0, 2.0], [1, -1], [-3, -2], [10.0, 20.0, 30.0])
    m3 = PortableGBDT(_model([tree3]))
    assert m3.predict_raw([0.0]) == pytest.approx(10.0)  # left->node1->left->leaf0
    assert m3.predict_raw([2.5]) == pytest.approx(30.0)  # left->node1->right (3>2) ->leaf1
    assert m3.predict_raw([1.5]) == pytest.approx(30.0)  # right->leaf2


def test_single_leaf_tree_root_leaf_flag():
    m = PortableGBDT(_model([_tree([], [], [], [], [0.7], root_leaf=True)]))
    assert m.predict_raw([1.0, 2.0]) == pytest.approx(0.7)
    assert m.predict_raw([-5.0]) == pytest.approx(0.7)
    assert m.leaf_indices([1.0]) == [0]


def test_base_score_is_folded_into_raw_score():
    m = PortableGBDT(_model([_tree([], [], [], [], [0.25], root_leaf=True)], base_score=-0.05))
    assert m.predict_raw([]) == pytest.approx(0.2)


def test_raw_score_sums_all_trees():
    trees = [_tree([0], [0.5], [-1], [-2], [0.1, 0.2]), _tree([1], [1.5], [-1], [-2], [0.3, 0.4])]
    m = PortableGBDT(_model(trees))
    assert m.predict_raw([0.4, 1.4]) == pytest.approx(0.1 + 0.3)
    assert m.predict_raw([0.6, 1.6]) == pytest.approx(0.2 + 0.4)


def test_predict_many_matches_per_row_predict():
    trees = [_tree([0], [0.5], [-1], [-2], [0.1, 0.2])]
    m = PortableGBDT(_model(trees))
    rows = [[0.1], [0.5], [0.9]]
    assert m.predict_many(rows) == [m.predict(r) for r in rows]


def test_check_features_rejects_mismatch():
    m = PortableGBDT(_model([_tree([], [], [], [], [0.1], root_leaf=True)], names=["a", "b"]))
    m.check_features(["a", "b"])  # identical names pass
    with pytest.raises(ModelFormatError, match="feature names differ"):
        m.check_features(["a", "c"])


def test_sigmoid_is_numerically_safe_at_extremes():
    assert sigmoid(0.0) == pytest.approx(0.5)
    assert sigmoid(800.0) == 1.0
    # for large negative z, exp(z) itself underflows to 0.0 in IEEE754 double, so
    # sigmoid(-800) is exactly 0.0 (this is not a bug, just subnormal underflow)
    assert sigmoid(-800.0) == 0.0
    assert sigmoid(-745.0) > 0.0  # last subnormal range before exp underflows entirely
    assert sigmoid(10.0) + sigmoid(-10.0) == pytest.approx(1.0)
    # a model with a huge negative raw score must not overflow
    m = PortableGBDT(_model([_tree([], [], [], [], [-1e6], root_leaf=True)]))
    assert m.predict([0.0]) == pytest.approx(0.0, abs=1e-12)
    assert 0.0 <= m.predict([0.0])


def test_load_from_file_sets_source(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps(_model([_tree([], [], [], [], [0.5], root_leaf=True)])), encoding="utf-8")
    m = PortableGBDT.load(p)
    assert m.source == str(p)
    assert m.detector == "TEST"
    assert m.n_trees == 1


def test_file_sha256_prefix(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_bytes(b"abc")
    assert file_sha256(p) == "sha256:" + __import__("hashlib").sha256(b"abc").hexdigest()


# ------------------------------------------------------------------ malformed models
def test_rejects_wrong_format_name():
    with pytest.raises(ModelFormatError, match="unsupported model format"):
        PortableGBDT(_model([], **{"format": "other-format"}))


def test_rejects_feature_index_out_of_range():
    tree = _tree([3], [0.5], [-1], [-2], [0.1, 0.2])  # feature 3 does not exist among 2 names
    with pytest.raises(ModelFormatError, match="feature index 3 out of range"):
        PortableGBDT(_model([tree]))


def test_rejects_child_index_out_of_range():
    tree = _tree([0], [0.5], [7], [-1], [0.1, 0.2])  # child 7 >= n_int (1)
    with pytest.raises(ModelFormatError, match="child 7 out of range"):
        PortableGBDT(_model([tree]))


def test_rejects_leaf_index_out_of_range():
    tree = _tree([0], [0.5], [-1], [-9], [0.1, 0.2])  # ~(-9) = 8 >= len(leaf)
    with pytest.raises(ModelFormatError, match="out of range"):
        PortableGBDT(_model([tree]))


def test_rejects_wrong_leaf_count():
    tree = _tree([0], [0.5], [-1], [-2], [0.1])  # one internal node needs exactly two leaves
    with pytest.raises(ModelFormatError, match="expected 2 leaves"):
        PortableGBDT(_model([tree]))


def test_rejects_mismatched_node_array_lengths():
    tree = _tree([0, 1], [0.5], [-1], [-2], [0.1, 0.2])  # feature has 2 entries, threshold 1
    with pytest.raises(ModelFormatError, match="different lengths"):
        PortableGBDT(_model([tree]))


def test_rejects_n_trees_mismatch():
    tree = _tree([0], [0.5], [-1], [-2], [0.1, 0.2])
    with pytest.raises(ModelFormatError, match="n_trees does not match"):
        PortableGBDT(_model([tree], **{"n_trees": 3}))


def test_rejects_root_leaf_tree_with_nodes_or_extra_leaves():
    bad = _tree([0], [0.5], [-1], [-2], [0.1, 0.2], root_leaf=True)
    with pytest.raises(ModelFormatError, match="root_leaf"):
        PortableGBDT(_model([bad]))
    bad2 = _tree([], [], [], [], [0.1, 0.2], root_leaf=True)
    with pytest.raises(ModelFormatError, match="root_leaf"):
        PortableGBDT(_model([bad2]))


def test_rejects_non_numeric_leaf_values():
    tree = _tree([], [], [], [], ["oops"], root_leaf=True)
    with pytest.raises(ValueError):
        PortableGBDT(_model([tree]))


def test_load_rejects_invalid_json(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        PortableGBDT.load(p)
