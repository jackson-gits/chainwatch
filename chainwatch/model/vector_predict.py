"""Vectorised numpy evaluator of the portable JSON trees (train env only, spec 14).

Evaluation uses the shipped JSON models through this module, so the scores in the report come from the same
trees the API loads. Arithmetic order matches ``PortableGBDT`` (trees summed in order, then a sigmoid).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


class VectorGBDT:
    def __init__(self, data: dict):
        self.data = data
        self.detector = data["detector"]
        self.feature_names = list(data["feature_names"])
        self.sigmoid_k = float(data.get("sigmoid", 1.0))
        self.base_score = float(data.get("base_score", 0.0))
        self.trees = []
        for t in data["trees"]:
            if t.get("root_leaf"):
                self.trees.append((None, None, None, None, np.array(t["leaf"], dtype=np.float64)))
            else:
                self.trees.append(
                    (
                        np.array(t["feature"], dtype=np.int64),
                        np.array(t["threshold"], dtype=np.float64),
                        np.array(t["left"], dtype=np.int64),
                        np.array(t["right"], dtype=np.int64),
                        np.array(t["leaf"], dtype=np.float64),
                    )
                )

    @classmethod
    def load(cls, path: str | Path) -> VectorGBDT:
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def predict_raw(self, X: np.ndarray, batch: int = 200_000) -> np.ndarray:
        X = np.ascontiguousarray(X, dtype=np.float64)
        n, ncol = X.shape
        out = np.empty(n, dtype=np.float64)
        for a in range(0, n, batch):
            Xb = X[a : a + batch]
            nb = Xb.shape[0]
            flat = Xb.reshape(-1)
            base = np.arange(nb, dtype=np.int64) * ncol
            s = np.full(nb, self.base_score, dtype=np.float64)
            for feat, thr, left, right, leaf in self.trees:
                if feat is None:
                    s += leaf[0]
                    continue
                idx = np.zeros(nb, dtype=np.int64)
                active = np.arange(nb)
                while active.size:
                    node = idx[active]
                    xv = flat[base[active] + feat[node]]
                    nxt = np.where(xv <= thr[node], left[node], right[node])
                    idx[active] = nxt
                    active = active[nxt >= 0]
                s += leaf[~idx]
            out[a : a + nb] = s
        return out

    def predict(self, X: np.ndarray) -> np.ndarray:
        z = self.sigmoid_k * self.predict_raw(X)
        out = np.empty_like(z)
        pos = z >= 0
        out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
        e = np.exp(z[~pos])
        out[~pos] = e / (1.0 + e)
        return out

    def leaf_indices(self, X: np.ndarray) -> np.ndarray:
        X = np.ascontiguousarray(X, dtype=np.float64)
        n, ncol = X.shape
        flat = X.reshape(-1)
        base = np.arange(n, dtype=np.int64) * ncol
        res = np.zeros((n, len(self.trees)), dtype=np.int64)
        for k, (feat, thr, left, right, _leaf) in enumerate(self.trees):
            if feat is None:
                continue
            idx = np.zeros(n, dtype=np.int64)
            active = np.arange(n)
            while active.size:
                node = idx[active]
                nxt = np.where(flat[base[active] + feat[node]] <= thr[node], left[node], right[node])
                idx[active] = nxt
                active = active[nxt >= 0]
            res[:, k] = ~idx
        return res
