"""Pure-Python evaluator for the portable gradient-boosted tree format ``chainwatch-gbdt-1`` (spec 10.3).

No third-party imports: this runs in the serving image on any architecture, including big-endian s390x,
because the model is plain JSON numbers and evaluation is IEEE-754 double arithmetic.

Tree encoding (flat arrays): node ``i`` tests ``x[feature[i]] <= threshold[i]``; true goes to ``left[i]``,
else ``right[i]``. A child ``>= 0`` is an internal node; a negative child ``c`` is leaf ``~c``.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path

FORMAT = "chainwatch-gbdt-1"


class ModelFormatError(ValueError):
    pass


def sigmoid(z: float) -> float:
    """Numerically safe logistic function."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class PortableGBDT:
    """Sum of tree outputs, then a sigmoid."""

    def __init__(self, data: dict, source: str | None = None):
        if data.get("format") != FORMAT:
            raise ModelFormatError(f"unsupported model format {data.get('format')!r}")
        self.data = data
        self.source = source
        self.detector: str = data["detector"]
        self.feature_names: list[str] = list(data["feature_names"])
        self.registry_hash: str = data.get("registry_hash", "")
        self.sigmoid_k: float = float(data.get("sigmoid", 1.0))
        self.base_score: float = float(data.get("base_score", 0.0))
        nf = len(self.feature_names)
        trees = []
        for t_i, tree in enumerate(data["trees"]):
            feat = [int(v) for v in tree.get("feature", [])]
            thr = [float(v) for v in tree.get("threshold", [])]
            left = [int(v) for v in tree.get("left", [])]
            right = [int(v) for v in tree.get("right", [])]
            leaf = [float(v) for v in tree["leaf"]]
            if tree.get("root_leaf"):
                if feat or len(leaf) != 1:
                    raise ModelFormatError(f"tree {t_i}: root_leaf tree must have no nodes and one leaf")
                trees.append((feat, thr, left, right, leaf, -1))
                continue
            n_int = len(feat)
            if not (len(thr) == len(left) == len(right) == n_int) or n_int == 0:
                raise ModelFormatError(f"tree {t_i}: node arrays have different lengths")
            if len(leaf) != n_int + 1:
                raise ModelFormatError(f"tree {t_i}: expected {n_int + 1} leaves, got {len(leaf)}")
            for f in feat:
                if not 0 <= f < nf:
                    raise ModelFormatError(f"tree {t_i}: feature index {f} out of range")
            for c in left + right:
                if (c >= 0 and c >= n_int) or (c < 0 and ~c >= len(leaf)):
                    raise ModelFormatError(f"tree {t_i}: child {c} out of range")
            trees.append((feat, thr, left, right, leaf, 0))
        if int(data.get("n_trees", len(trees))) != len(trees):
            raise ModelFormatError("n_trees does not match the number of trees")
        self._trees = tuple(trees)
        self.n_trees = len(trees)

    @classmethod
    def load(cls, path: str | Path) -> PortableGBDT:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(data, source=str(path))

    def check_features(self, names: Sequence[str]) -> None:
        if list(names) != self.feature_names:
            raise ModelFormatError(f"{self.detector}: feature names differ from the model's feature_names")

    def predict_raw(self, x: Sequence[float]) -> float:
        s = self.base_score
        for feat, thr, left, right, leaf, i in self._trees:
            while i >= 0:
                i = left[i] if x[feat[i]] <= thr[i] else right[i]
            s += leaf[~i]
        return s

    def predict(self, x: Sequence[float]) -> float:
        return sigmoid(self.sigmoid_k * self.predict_raw(x))

    def predict_many(self, rows) -> list[float]:
        return [self.predict(x) for x in rows]

    def leaf_indices(self, x: Sequence[float]) -> list[int]:
        out = []
        for feat, thr, left, right, _leaf, i in self._trees:
            while i >= 0:
                i = left[i] if x[feat[i]] <= thr[i] else right[i]
            out.append(~i)
        return out


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()
